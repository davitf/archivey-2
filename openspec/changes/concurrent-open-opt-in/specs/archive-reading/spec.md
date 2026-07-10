# Archive Reading — delta (concurrent-open-opt-in)

## MODIFIED Requirements

### Requirement: Opening an archive for reading

The system SHALL expose a top-level `archivey.open_archive()` function that accepts a file path, `Path`, or binary stream and returns an `ArchiveReader`.

```python
archivey.open_archive(
    source: str | Path | BinaryIO | Sequence[str | Path | BinaryIO],
    *,
    format: ArchiveFormat | None = None,   # override detection
    streaming: bool = False,               # False = random access; True = forward-only one pass
    allow_multiple_open_streams: bool = False,  # opt in to holding >1 member stream open at once
    password: str | bytes | Sequence[str | bytes] | PasswordProvider | None = None,
    encoding: str | None = None,           # None = auto-detect member-name encoding
    config: ArchiveyConfig | None = None,  # None = the library default configuration
) -> ArchiveReader
```

The `format` parameter MAY be omitted; when omitted the library performs automatic
format detection. `encoding` defaults to `None`, meaning the library auto-detects the
encoding of member-name fields: it uses the **format's internal signal** when present
(e.g. the ZIP UTF-8 general-purpose-bit, RAR5 UTF-8 names, tar PAX UTF-8 records), and
otherwise detects from the raw name bytes. A caller MAY pass an explicit `encoding` as a
last-resort override when the format records none and detection is unreliable; the
verbatim bytes are always preserved in `ArchiveMember.raw_name` so names can be
re-decoded losslessly. `source` MAY also be an ordered sequence of files/streams that
together form a single multi-volume archive (see the multi-volume requirement below).
`password` accepts a single value, an ordered sequence of candidate values, or a
provider callable (see the password requirement below). `config` carries the library's
tuning/policy knobs (see the configuration requirement below); per-call operational
arguments remain keyword parameters and MUST NOT move into the config object. The
Phase 4 `strict_eof` keyword is removed — end-of-archive strictness lives at
`config.strict_archive_eof`.

`allow_multiple_open_streams` (default `False`) declares that the caller intends to hold
**more than one member stream open at the same time** and read them interleaved. It is
valid only with random-access mode (`streaming=False`). Combining
`streaming=True` with `allow_multiple_open_streams=True` SHALL raise at `open_archive()`
(a typed argument / usage error — not deferred to a later `open()`). When left `False`, the
reader permits at most one live member stream at a time (see *Multiple concurrently-open
member streams*); it is a per-call operational keyword and MUST NOT move into the config
object.

#### Scenario: open with auto-detected format

- **WHEN** `archivey.open_archive("archive.tar.gz")` is called with no `format` override
- **THEN** the library detects the format from magic bytes and returns an `ArchiveReader` wrapping the appropriate backend

#### Scenario: open with explicit format override

- **WHEN** `archivey.open_archive(source, format=ArchiveFormat.ZIP)` is called
- **THEN** the library uses the specified format backend without running detection

#### Scenario: open with password

- **WHEN** `archivey.open_archive(source, password="secret")` is called
- **THEN** the returned `ArchiveReader` uses the provided password for encrypted members

#### Scenario: open opting in to multiple concurrent streams

- **WHEN** `archivey.open_archive(source, allow_multiple_open_streams=True)` is called
- **THEN** the returned reader permits holding several member streams open at once and reading them interleaved (see *Multiple concurrently-open member streams*)

#### Scenario: streaming plus multiple-open-streams is rejected at open

- **WHEN** `archivey.open_archive(source, streaming=True, allow_multiple_open_streams=True)` is called
- **THEN** a typed error is raised immediately and no reader is returned

### Requirement: Multiple concurrently-open member streams

Holding **more than one member stream open at the same time** from a single reader SHALL be an
**opt-in** capability, gated uniformly across all formats by the
`allow_multiple_open_streams` flag (see *Opening an archive for reading*). The gate is
format-independent by design: it fires on ZIP and plain TAR (where concurrency would be
cheap) exactly as it does on a compressed TAR or a solid 7z (where it can be expensive),
so the constraint is deterministic across formats and is discovered in **development**,
regardless of the test corpus, rather than deferred to production. `AccessCost` /
`solid_block_count` are **informational** about whether opted-in interleaving is cheap or
a re-decompression storm; they never gate legality.

**Default (`allow_multiple_open_streams=False`): at most one live stream.** A member stream
is **live** from `open()` until it is `close()`d (or its `with` block exits). Opening a
second member stream while another is still live SHALL raise a typed `ConcurrentAccessError`,
uniformly for every backend. The gate counts **simultaneously-live** streams: the ordinary
`open → read → close → open next` sequence (one live at a time) is always allowed, on every
format. Liveness ends only at `close()` — **not** at end-of-stream (member streams may be
seekable and re-read) and **not** at garbage collection (which would make the gate
non-deterministic).

**Raise, never auto-close.** The second overlapping `open()` SHALL raise and leave the first
stream **untouched and still readable**; the reader MUST NOT silently close or invalidate a
stream the caller still holds. (Silently closing it would defer the failure to a later read on
the first stream, far from the second `open()` that caused it — the failure mode "no surprises"
forbids.) The `ConcurrentAccessError` message SHALL direct the caller to close the open stream
(e.g. via `with`) or to pass `allow_multiple_open_streams=True`, noting that interleaving a
solid archive re-decompresses.

**Opted-in (`allow_multiple_open_streams=True`): correct interleaving.** The reader SHALL
support any number of member streams held open and read in **interleaved** order without
corrupting one another.

**Archivey-owned byte ranges.** A backend that serves members by **byte-range access into a
shared source that archivey addresses** (e.g. native 7z/RAR, single-file) MUST route member
reads through a shared-source view that keeps a **per-view position** and performs each
seek+read as an atomic pair under the source lock.

**Library-owned seek-before-read.** A backend whose external library re-seeks a shared handle
on each member `read()` but does not lock across that pair (random-access TAR via `tarfile`,
ISO via `pycdlib`) MUST wrap each member stream so every data-path read holds a **per-archive
lock** for the duration of the library `read` (mechanism owned by `tar-concurrent-open`). ZIP
via stdlib `zipfile._SharedFile` already provides that lock and needs no archivey wrap.

**Solid formats.** A **solid** format (7z folder, RAR block, compressed TAR's single stream)
satisfies the requirement by giving each `open()` its own decompressor / logical member stream
over the shared source, re-decoding or re-seeking as the *random open on a solid member*
scenario already permits — which is where the interleaving expense lives, and why the
capability is opt-in.

**Streaming carve-out and relation to `stream_members`.** Forward-only streaming readers
(`streaming=True`) are a single forward pass and are out of scope for this opt-in (the
invalid combination with `allow_multiple_open_streams=True` is rejected at `open_archive()`).
The existing `stream_members()` rule — the previous yielded stream becomes invalid when the
iterator advances — is a **separate** contract and MUST NOT be changed to match the
raise-never-auto-close policy of random-access `open()`. Rationale: `stream_members` is a
sequential one-pass API; auto-releasing the previous stream on advance is what makes
`for member, stream in reader.stream_members(): …` safe when the caller ignores `stream`.
Applying raise-on-overlap there would make that loop depend on GC reclaiming the unused
stream before the next yield — non-deterministic and hostile to the common pattern.
Random-access `open()` has the opposite mental model (independent handles the caller
controls), so it raises instead of silently invalidating.

**Thread-safe member open/read when opted in.** When `allow_multiple_open_streams=True` and
the member list has been materialized (see *Materialize-before-fan-out* on the reentrancy
requirement), concurrent calls from multiple threads to `open()`, member-stream `read` /
`close`, and the live-stream gate bookkeeping SHALL be safe (synchronized). Concurrent
`ArchiveReader.close()`, `__iter__` / `stream_members` / progressive listing, or any use
under `streaming=True` remains **unsupported** (single owner thread for those operations).
This is the contract that lets a caller hand one opted-in reader to several workers that
open members at will after an upfront `members()` (or equivalent) pass.

**Single-reader lifecycle still owned by one thread.** The unsupported cases above are not
a blanket "reader is never thread-safe"; they are the operations that still mutate
forward-pass / close state. Member open/read under the opt-in is the supported parallel
seam.
**Failing loudly on detectable misuse.** Where a shared-source view *can* detect misuse — a read
after the source is closed — the reader surface SHALL raise a typed error (translated from the
primitive's stdlib-shaped error at the reader boundary). A view whose requested bounds extend
past the source is **clamped** to the available bytes (like a real stream), so a truncated
archive still yields a short readable view rather than failing at construction.

#### Scenario: a second overlapping open raises by default, uniformly across formats

- **WHEN** a reader opened with the default `allow_multiple_open_streams=False` has one member
  stream still open and `open()` is called for a second member
- **THEN** `ConcurrentAccessError` is raised — identically whether the archive is ZIP, plain
  TAR, `.tar.gz`, or solid 7z (the gate does not vary by format or cost)

#### Scenario: the sequential open/read/close loop is always allowed

- **WHEN** a default reader opens a member, reads and closes it, then opens the next, repeatedly
- **THEN** no error is raised on any format (only one stream is ever live at a time)

#### Scenario: the first stream survives a rejected second open

- **WHEN** a default reader holds member A's stream open, `open(B)` raises `ConcurrentAccessError`
- **THEN** A's stream is untouched and still readable (the reader did not auto-close it)

#### Scenario: reopening after close is allowed

- **WHEN** a default reader opens a member, closes that stream, then opens another member
- **THEN** the second open succeeds (liveness ended at close, not at end-of-stream or GC)

#### Scenario: opting in enables correct interleaving

- **WHEN** a reader opened with `allow_multiple_open_streams=True` has two members of a
  byte-range random-access archive open at once and read in an interleaved sequence of partial
  reads
- **THEN** each stream returns exactly its own member's bytes in order, regardless of the
  interleaving, and neither is affected by the other's position

#### Scenario: opted-in interleaving on a solid archive is allowed but may re-decompress

- **WHEN** a reader opened with `allow_multiple_open_streams=True` interleaves two members of a
  solid archive (e.g. `.tar.gz` or one 7z folder)
- **THEN** the reads are correct and no `ConcurrentAccessError` is raised; the cost is reflected
  by `AccessCost.SOLID` / `solid_block_count`, and re-decompression may occur as the solid-open
  cost model already permits

#### Scenario: random-access TAR supports interleaved opens (opted-in)

- **WHEN** a random-access TAR reader (`streaming=False`) is opened with
  `allow_multiple_open_streams=True` and two file members are read interleaved
- **THEN** each stream returns its own member's bytes correctly (plain and compressed TAR)

#### Scenario: ISO supports interleaved opens (opted-in)

- **WHEN** an ISO reader is opened with `allow_multiple_open_streams=True` and two file
  members are read interleaved
- **THEN** each stream returns its own member's bytes correctly

#### Scenario: reading after the reader is closed fails loudly

- **WHEN** a member stream is read after its reader (and the underlying source) has been closed
- **THEN** a typed error is raised at the reader surface rather than returning arbitrary or empty bytes

### Requirement: Random-access member-open is reentrant and reader-state-free

The system SHALL require that, for a **random-access backend that advertises independent member
open** (`streaming=False` — e.g. the native 7z/RAR readers, ZIP, single-file, random-access TAR,
ISO), the member-open implementation (`_open_member`) derives the member's bytes from the
member and the archive's shared source / library handle without **unsafe** shared-state
mutation: it MUST NOT keep per-open scratch on the reader that a second concurrent open would
overwrite, and any mutation of shared reader state that another concurrent open depends on
MUST be properly synchronized (e.g. under a lock). Backends MAY hold immutable,
already-materialized state (the member list and name index) read-only. Reader-level lifecycle
bookkeeping that tracks *which* member streams are currently open (to enforce the
`allow_multiple_open_streams` gate) is permitted when synchronized; it is not per-open scratch,
because it does not feed the bytes a concurrent open returns.

**What this prevents.** The classic failure is a field like "the one pending stream" or "the
current decompressor" on `self` that `open()` replaces — a second open silently corrupts the
first. Synchronized bookkeeping and SharedSource / locked library streams are the allowed
shape; unprotected read-modify-write of open-critical state is not.

**Archivey-owned byte ranges.** Any byte-range access archivey performs MUST go through a
shared-source view (per the *Multiple concurrently-open member streams* requirement) rather
than by seeking a single shared handle in place.

**Library-owned seek-before-read.** Backends that open members through an external library that
re-seeks a shared handle on each `read()` (TAR via `tarfile.extractfile`, ISO via `pycdlib`)
MUST NOT be required to route through an archivey shared-source view; they MUST still avoid
per-open scratch on archivey-owned reader state and MUST apply the per-archive locked
member-stream wrap (or rely on a library-provided equivalent, as ZIP does).

**Scope.** This invariant does **not** apply to forward-only/streaming reads (`streaming=True`),
which are inherently single-pass. Together with the opted-in thread-safe open/read contract
above, it is what makes handing one reader to multiple workers viable after an upfront member
list materialization.

**Materialize-before-fan-out.** A concurrent consumer (including multi-threaded workers) MUST
materialize the member list (a completed random-access pass) before opening members
concurrently from multiple threads; the one-time member-cache build MUST be concurrency-safe
when the opt-in is enabled (init-under-lock or equivalent), so a race on first `members()`
cannot corrupt the cache. Single-threaded opted-in interleaving MAY rely on an already-built
cache without an extra lock if the caller materialized first.

#### Scenario: opening one member does not disturb another open

- **WHEN** a random-access backend that advertises independent member open serves two members
  opened concurrently (opted-in) from the same reader
- **THEN** neither open call has performed an unsafe mutation of reader state the other depends
  on, and each returned stream reads its own member's bytes correctly under interleaving

#### Scenario: archivey-owned member-open derives access from the shared source

- **WHEN** an archivey-owned byte-range backend implements `_open_member`
- **THEN** it obtains the member's byte range through a shared-source view rather than by seeking
  a single shared handle in place, so concurrent opens cannot corrupt each other's position

#### Scenario: library-owned TAR and ISO use locked member streams; streaming is out of scope

- **WHEN** the backend is random-access TAR or ISO
- **THEN** `_open_member` returns a library member stream wrapped so data-path reads hold the
  per-archive lock, and archivey-owned reader state holds no unprotected per-open scratch
- **WHEN** the reader is a forward-only streaming pass
- **THEN** this invariant does not apply and the backend is not required to support concurrent
  member opens

#### Scenario: opted-in workers can open members concurrently after materialize

- **WHEN** a reader is opened with `allow_multiple_open_streams=True`, `members()` (or
  equivalent) has completed, and two threads each call `open()` for different members and read
- **THEN** both streams return correct bytes and no data race corrupts reader state
