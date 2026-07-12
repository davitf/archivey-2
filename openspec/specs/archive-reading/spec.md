# Archive Reading

## Purpose

Provides a uniform interface for opening and reading archives across all supported formats. The `ArchiveReader` class presents ZIP, TAR, RAR, 7z, ISO, plain directories, and single-file compressed streams as interchangeable objects with consistent metadata, iteration, and data-access semantics.

## Requirements

### Requirement: Opening an archive for reading

The system SHALL expose:

```python
archivey.open_archive(
    source: str | Path | BinaryIO | Sequence[str | Path | BinaryIO],
    *,
    format: ArchiveFormat | None = None,
    streaming: bool = False,
    password: PasswordInput = None,
    encoding: str | None = None,
    config: ArchiveyConfig | None = None,
) -> ArchiveReader
```

`source`, multi-volume ordering, `streaming`, password candidates/providers, encoding,
configuration precedence, and backend selection retain their existing contracts.
`format=None` performs automatic detection; an explicit format bypasses detection.

The implementation SHALL create the prospective reader's one collector, budget, and
initial operation watermark before detection or backend open. Automatic detection SHALL
receive that collector. On successful open, the returned reader assumes ownership of the
same collector; opening SHALL NOT seed, merge, replay, or copy detection events into a
second collector. An occurrence retained during detection therefore consumes exactly one
aggregate budget slot and keeps one occurrence id/order position in the reader lifetime.
If detection/open raises, no reader is returned and the temporary collector is discarded
after the exception propagates.

#### Scenario: open with automatic detection transfers ownership

- **WHEN** `open_archive()` automatically detects a format and successfully builds its reader
- **THEN** the reader owns the exact collector used during detection, including its counters and retained entries, without duplicated references

#### Scenario: explicit format has no detection events

- **WHEN** `open_archive(source, format=ArchiveFormat.ZIP)` succeeds
- **THEN** one collector still covers open and later work, but detection is not run and no detection diagnostic is recorded

#### Scenario: open with password

- **WHEN** `archivey.open_archive(source, password="secret")` is called
- **THEN** the returned `ArchiveReader` uses the provided password for encrypted members

### Requirement: Declared member-stream capabilities

`open_archive()` SHALL accept `member_streams: MemberStreams`, a flags enum with two
capabilities, defaulting to none:

- `MemberStreams.CONCURRENT` — any number of member streams may be open simultaneously.
- `MemberStreams.SEEKABLE` — member streams are seekable where the backend can provide
  it.

**Default contract (no capability declared), uniform across every format including the
directory reader:** at most one member data stream may be live per reader, and member
streams are forward-only. "Live" spans `open()` to the stream's `close()`/context exit —
not EOF and not garbage collection. Opening a second overlapping stream SHALL raise
`ConcurrentAccessError` at the later `open()` and SHALL leave the first stream untouched
and readable; the library never silently closes or invalidates a stream the caller still
holds. Every member stream (random `open()` and `stream_members()` yields alike) SHALL
report `seekable() is False`; `seek()` SHALL raise `io.UnsupportedOperation`; `tell()`
SHALL work. The ordinary `open → read → close → open next` loop is unaffected.

`open_archive()` SHALL record its caller's stack (captured once at open) and
`ConcurrentAccessError` SHALL include the caller's `file:line`, so the error points at
where the capability should have been declared. The full captured stack is retained on
the reader for diagnostics; there is no separate config knob.

The capability declaration is per-archive intent: there SHALL be no `ArchiveyConfig`
equivalent and no per-`open()` capability argument. Access cost never determines
legality: declared capabilities are honored on every format, and the cost receipt
describes what they cost on this archive.

**Internal operations are exempt from the gate.** `extract_all()` (including hardlink
recovery), symlink-target reads, password candidate confirmation, and other
library-internal member opens run under internal scopes and SHALL NOT require any
declared capability. No caller flag is ever needed to extract.

**Out of the gate's scope.** The cost of a caller's member *open order* on a solid
archive (random opens each re-decoding from the block start, with no overlapping
lifetimes) is not gated: it remains governed by `AccessCost`/`solid_block_count` and the
`stream_members()` steer. Documentation for `member_streams` SHALL state this
explicitly.

#### Scenario: second overlapping open without CONCURRENT fails uniformly

- **WHEN** a reader opened without `MemberStreams.CONCURRENT` — on ZIP, TAR, ISO,
  single-file, or a directory alike — has one member stream open and `open()` is called
  for another member
- **THEN** `ConcurrentAccessError` is raised at the second `open()`, its message includes
  the `file:line` where `open_archive()` was called, and the first stream remains
  readable

#### Scenario: sequential open-read-close needs no declaration

- **WHEN** a caller opens a member, reads it, closes it, and opens the next member, with
  no declared capabilities
- **THEN** every open succeeds; non-overlapping lifetimes never trigger the gate

#### Scenario: undeclared streams are forward-only on every format

- **WHEN** a member stream is obtained from a reader opened without
  `MemberStreams.SEEKABLE` — including a directory member that is a real file
- **THEN** `seekable()` is `False` and `seek()` raises `io.UnsupportedOperation`, while
  `tell()` and forward reads work normally

#### Scenario: declared SEEKABLE restores seekability where the backend provides it

- **WHEN** the same member is opened from a reader declared with `MemberStreams.SEEKABLE`
- **THEN** the stream is seekable where the backend can provide positioning, and the
  seekable-decompressor-streams loud-slow-rewind rule governs the non-accelerated path

#### Scenario: extraction requires no declared capability

- **WHEN** `extract_all()` runs (including its hardlink recovery pass and symlink-target
  reads) on a reader with no declared capabilities
- **THEN** it completes normally; internal member opens are not gated

### Requirement: Multi-volume and multi-source input

`open_archive()` SHALL accept a multi-volume archive through either of two paths, and
present it as one logical `ArchiveReader`:

- **From a single path that is part of a volume set** (e.g. `name.7z.001`,
  `name.part1.rar`, or `name.rar` + `name.r00`…): the library discovers the sibling
  volumes in their natural order and treats them as one archive.
- **From an explicit ordered sequence** of files/streams passed as `source`: the
  library uses them, in the given order, as the volumes of one archive.

Volume joining is format-specific (see `format-7z` and `format-rar`): a 7z set is a
single byte stream split across parts and is concatenated; a RAR set is a sequence of
self-describing volumes whose headers are parsed in order and whose
boundary-spanning members are stitched together. When the set is incomplete or out of
order, the library SHALL raise `UnsupportedFeatureError` or a truncated/corrupt error
rather than returning a partial result.

#### Scenario: open a volume set from one of its parts

- **WHEN** `archivey.open_archive("disc.7z.001")` is called and the sibling `.7z.NNN` volumes are present alongside it
- **THEN** the returned reader exposes the members of the whole multi-volume archive as if it were a single file

#### Scenario: open a volume set from an explicit list

- **WHEN** `archivey.open_archive([vol1, vol2, vol3])` is called with the volumes in order
- **THEN** the reader treats them as one archive in that order

#### Scenario: incomplete volume set

- **WHEN** a volume is missing from the set
- **THEN** `open_archive()` (or the first dependent read) raises `UnsupportedFeatureError` or a truncated/corrupt error rather than a partial member list

---

### Requirement: Archive metadata access

The system SHALL expose three read-only properties on `ArchiveReader` for archive-level metadata.

```python
@property
def info(self) -> ArchiveInfo: ...

@property
def cost(self) -> CostReceipt: ...

@property
def format(self) -> ArchiveFormat: ...
```

`info` returns an `ArchiveInfo` dataclass (format, version, solid flag, member count, comment, encryption, multivolume status, and cost). `cost` returns a `CostReceipt` describing the listing cost, access cost, stream capability, and solid block count. `format` returns the `ArchiveFormat` `(container, stream)` value for the open archive.

#### Scenario: access info after open

- **WHEN** an archive is successfully opened
- **THEN** `ar.info`, `ar.cost`, and `ar.format` are immediately available without triggering additional I/O

---

### Requirement: Sequential in-order iteration

The system SHALL support iterating all members in archive order via `__iter__`, MAY
materialize the full member list via `members()`, and SHALL provide `scan_members()` —
a mode-agnostic way to obtain the fully-resolved member list.

```python
def __iter__(self) -> Iterator[ArchiveMember]: ...     # sequential, in-order
def members(self) -> list[ArchiveMember]: ...          # materializes all (random-access only; may scan)
def scan_members(self) -> list[ArchiveMember]: ...      # fully-resolved list, either mode
def get_members_if_available(self) -> list[ArchiveMember] | None: ...  # index-only peek
```

`__iter__` MUST yield `ArchiveMember` objects one at a time in archive order without
loading all members into memory. In **random-access** mode, `members()` MAY trigger a full
scan for streaming formats that have no central directory, and after the member list has
been materialized once, subsequent `__iter__` calls MUST return from the cache rather than
re-reading the archive. In **streaming** mode there is no such cache-replay: `__iter__` is
part of the single forward pass and is single-use (see the forward-only requirement below
and `access-mode-and-cost`).

`scan_members()` SHALL return the **fully-resolved** member list — every link's
`link_target_member` filled where the target exists, including forward-pointing symlinks
and true last-wins symlinks. In random-access mode it is equivalent to `members()`. On a
`streaming=True` reader it SHALL return the cache if the single forward pass has already
completed, otherwise **finish that pass** — running it from the start, or draining the
remainder of an *interrupted* one — resolving all links, and returning the complete list.
`scan_members()` is the only method permitted after an iteration method has started;
running it consumes/finishes the pass (see `access-mode-and-cost`).

During a live forward pass a forward-pointing symlink is unresolved at the moment it is
yielded (a single pass cannot see ahead); the system finalizes resolution when the pass
reaches its end. Completing a forward pass — via `__iter__`, `stream_members`,
`extract_all`, or `scan_members` — SHALL finalize the fully-resolved member cache: the
system runs full link resolution over all members (filling forward-pointing and last-wins
symlink targets **in place** on the objects already yielded, per the mutable-member
contract) and records the list so `get_members_if_available()` returns it thereafter. A
forward pass abandoned before completion (an early `break`, with no subsequent
`scan_members()`) SHALL NOT finalize the cache.

The reader deliberately defines **no `__len__`** (and no `__getitem__` — see the
name-lookup requirement): the reader is not a collection, and the sequence/mapping
protocols get probed *implicitly* in ways the library does not control (`list(reader)`
probes `__len__` for preallocation via the length-hint protocol). `len(reader)` therefore
raises Python's own `TypeError` in every mode; a caller that wants a count uses
`len(ar.members())`, `ar.info.member_count` (when cheaply known), or counts during
iteration. `list(reader)` just iterates.

`get_members_if_available()` is **index-only**: it returns the member list only when it is
available without scanning and without reading member data (an upfront index, or an
already-materialized cache), else `None`; it never scans and never begins the forward pass,
so it is callable under any intent. Because it reads no member data, members it returns may
have unresolved links for formats that store link targets in member data (see
`access-mode-and-cost` for its full contract).

When opened with `streaming=True`, the reader is forward-only: `members()`, `get()`,
`open()`, and `read()` all SHALL raise `UnsupportedOperationError` (uniformly, not
depending on a loaded index). Only a single forward pass — `__iter__`/`stream_members` or
one `extract_all` — is allowed, with `scan_members()` permitted to finish or return it, and
`get_members_if_available()` callable at any time. See the access mode × method table in
`access-mode-and-cost`.

#### Scenario: forward iteration

- **WHEN** `for member in ar` is executed
- **THEN** the reader yields `ArchiveMember` objects in archive order without buffering all of them in memory

#### Scenario: materialization on a streaming reader

- **WHEN** `ar.members()` is called on a reader opened with `streaming=True`
- **THEN** `UnsupportedOperationError` is raised

#### Scenario: streaming iteration is single-use

- **WHEN** a `streaming=True` reader is iterated once via `__iter__` (to completion or with an early `break`), then any of `__iter__` / `stream_members` / `extract_all` is called again
- **THEN** `UnsupportedOperationError` is raised (there is no streaming cache-replay of `__iter__`)

#### Scenario: scan_members() on a streaming reader returns the fully-resolved list

- **WHEN** `ar.scan_members()` is called on a not-yet-iterated `streaming=True` reader of a no-index format (e.g. a streaming tar) whose archive contains a symlink pointing at a *later* member
- **THEN** the full member list is returned with that forward-pointing symlink's `link_target_member` resolved
- **AND** the single forward pass is now consumed, so a subsequent `stream_members()` / `__iter__` / `extract_all` raises `UnsupportedOperationError`

#### Scenario: scan_members() finishes an interrupted iteration

- **WHEN** a `streaming=True` reader of a no-index format is iterated with an early `break`, then `ar.scan_members()` is called
- **THEN** it drains the remainder of the single pass internally and returns the complete, fully-resolved member list

#### Scenario: scan_members() equals members() in random-access mode

- **WHEN** `ar.scan_members()` is called on a `streaming=False` reader
- **THEN** it returns the same fully-resolved member list as `ar.members()`, and the reader remains usable for random access (nothing is consumed)

#### Scenario: get_members_if_available() returns the list after a completed streaming pass

- **WHEN** a `streaming=True` reader of a no-index format is iterated to completion via `__iter__` (or `stream_members`), then `ar.get_members_if_available()` is called
- **THEN** the fully-resolved member list is returned (not `None`), and any forward-pointing symlink resolved during finalization is now visible on the objects yielded during the pass

#### Scenario: abandoned partial pass does not materialize

- **WHEN** a `streaming=True` reader of a no-index format is iterated but the loop `break`s before the last member and `scan_members()` is not called, then `ar.get_members_if_available()` is called
- **THEN** `None` is returned (the pass did not complete, so no full listing is claimed)

#### Scenario: no len(); list() iterates

- **WHEN** `len(ar)` is called on any reader
- **THEN** Python's own `TypeError` is raised (`ArchiveReader` defines no `__len__`)
- **AND** `list(ar)` iterates normally (on a streaming reader, consuming the single forward pass)

---

### Requirement: Name lookup and member identity

The system SHALL provide name lookup through the explicit `get()` method — the reader is
deliberately **not** a mapping (no `__getitem__`): duplicate member names mean the mapping
contract cannot be honored, and dunder protocols get probed implicitly in ways the library
does not control. `open()`/`read()` also accept a name directly and raise `KeyError` when
it is absent.

```python
def get(self, name: str, default=None) -> ArchiveMember | None: ...
def __contains__(self, member: ArchiveMember) -> bool: ...   # identity, O(1), any mode
```

`get()` looks up a member by its normalized name; with duplicate names it returns the
**last** one (the member a sequential extraction would leave on disk — callers needing all
duplicates iterate). Calling `get` on a reader opened with `streaming=True` SHALL raise
`UnsupportedOperationError` — uniformly, regardless of whether the backend has an index
loaded (a streaming reader is forward-only; this keeps its behaviour deterministic across
formats). A caller that wants a no-scan peek at the member list on any reader uses
`get_members_if_available()` instead.

`member in reader` is **identity membership**: `True` iff the `ArchiveMember` object was
yielded by this reader. It is O(1) (no scan), so it is valid in any access mode; its use
is disambiguating members when several readers are in play. A non-`ArchiveMember` operand
(notably a name string) SHALL raise `TypeError` directing the caller to `get()`.
`__contains__` MUST be defined even though it is a convenience: without it, Python's `in`
operator falls back to iterating `__iter__`, which would silently consume a streaming
reader's single forward pass and compare members by value.

#### Scenario: successful name lookup

- **WHEN** `ar.get("path/to/file.txt")` is called and the member exists
- **THEN** the corresponding `ArchiveMember` object is returned

#### Scenario: missing name lookup

- **WHEN** `ar.get("nonexistent.txt")` is called and the member does not exist
- **THEN** `None` (or the caller's `default`) is returned
- **AND** `ar.open("nonexistent.txt")` / `ar.read("nonexistent.txt")` raise `KeyError`

#### Scenario: name lookup on a streaming reader

- **WHEN** `ar.get("file.txt")` is called on a reader opened with `streaming=True`
- **THEN** `UnsupportedOperationError` is raised (regardless of any loaded index)

#### Scenario: identity membership

- **WHEN** `member in ar` is evaluated with an `ArchiveMember` yielded by `ar`
- **THEN** it is `True` (and `False` for a member from a different reader), without scanning, in either access mode

#### Scenario: string operand for `in` is rejected

- **WHEN** `"file.txt" in ar` is evaluated
- **THEN** `TypeError` is raised, directing the caller to `ar.get(name)` (the `in` operator never falls back to iterating the reader)

---

### Requirement: Reading member data

The public `ArchiveStream` SHALL implement the `BinaryIO` contract, remain caller-closed,
and additionally expose an immutable operation-filtered diagnostic snapshot:

```python
class ArchiveStream(BinaryIO):
    @property
    def diagnostics(self) -> DiagnosticSummary: ...

def read(self, member: str | ArchiveMember) -> bytes: ...
def open(self, member: str | ArchiveMember) -> ArchiveStream: ...
```

Both methods accept a name or an `ArchiveMember` yielded by this reader. An unknown name
raises `KeyError`; a foreign member raises `ValueError`. `read()` materializes the entire
payload without extraction bomb checks and is intended for small trusted members.
`open()` streams in bounded chunks. Full reads verify any supported member digest;
streaming verification raises `CorruptionError` on the terminal read only after all valid
chunks have been delivered, while `read()` raises without returning bytes.

A reader-owned stream SHALL use an operation token/watermark over the reader's collector.
It SHALL NOT own or retain a second copy of its diagnostics. A standalone
`ArchiveStream` not owned by a reader SHALL own one stream-lifetime collector.

#### Scenario: opening a member returns the diagnostic stream type

- **WHEN** `reader.open("data.bin")` succeeds
- **THEN** it returns an `ArchiveStream` usable as `BinaryIO`, and `stream.diagnostics` reports only that stream operation's events

#### Scenario: stream and reader do not duplicate retention

- **WHEN** a reader-owned stream emits a rewind diagnostic
- **THEN** stream and reader snapshots can both expose it while the shared collector retains and charges it only once

#### Scenario: reading member as bytes

- **WHEN** `ar.read("readme.txt")` is called
- **THEN** the full uncompressed content is returned as `bytes`

#### Scenario: opening a member from a different reader is rejected

- **WHEN** `ar.open(member)` is called with an `ArchiveMember` yielded by a *different* reader
- **THEN** `ValueError` is raised (never data from the wrong entry)

### Requirement: Bounded-memory sequential streaming via stream_members

The system SHALL provide:

```python
def stream_members(
    self,
    members: MemberSelector | None = None,
) -> Iterator[tuple[ArchiveMember, ArchiveStream | None]]: ...
```

It yields `(member, stream)` pairs in archive order with bounded memory. A solid block is
decompressed progressively and never buffered whole in memory; peak memory is the decoder
working set plus one in-flight chunk. Non-file members yield `None`.

`members` is a selector (a collection of names/member identities or a predicate), not a
transform. Streams are lazy: unselected or unread members are not opened/decompressed and
do not request passwords. The generator yields the original mutable `ArchiveMember` so
late-bound fields remain visible; transformation stays at extraction/writing sinks.

The yielded stream is owned by the iterator and valid only until advance: before obtaining
the next item, the iterator SHALL close/invalidate the previous stream. The implementation
MUST NOT retain a growing decompressed-block cache until reader close. On a solid archive,
random `open()` may re-decode from the block start and warn callers to prefer
`stream_members()` for a sequential pass.

A `stream_members()` invocation is an exclusive one-pass/data-path operation in both access
modes. It SHALL NOT overlap random `open()`, materialization, another iteration/data pass,
an unrelated extraction, or reader close. An `extract_all()` owner MAY invoke it as a child
pass and MAY read/close the yielded child stream. Detected unrelated overlap SHALL raise
`ArchiveyUsageError` at the later operation and leave the active pass/stream valid.
This differs deliberately from random `open()`, whose independently owned streams may
coexist.

Each yielded stream is an operation-filtered view over the reader's single collector and
budget; advancing the iterator does not create a new diagnostic collector or aggregate
copy.

#### Scenario: sequential stream has public diagnostics

- **WHEN** a yielded file stream encounters a diagnostic before the iterator advances
- **THEN** the stream snapshot and cumulative reader snapshot expose that occurrence from one retained aggregate entry

#### Scenario: skipped member data is never opened

- **WHEN** a selector excludes a member or the caller does not read its yielded stream
- **THEN** that member's data is not opened/decompressed and no data-path diagnostic is produced for it

#### Scenario: streaming a solid archive

- **WHEN** `ar.stream_members()` is called on a solid archive (e.g. 7z)
- **THEN** each solid block is decompressed progressively as its members are consumed and released as the iterator advances, never buffered whole in advance
- **AND** peak memory is the decompressor working state plus one in-flight chunk, not a whole solid block

#### Scenario: selecting members while streaming

- **WHEN** `ar.stream_members(lambda m: m.name.endswith(".txt"))` is called
- **THEN** only `.txt` members are yielded as original mutable `ArchiveMember` objects, and streams for unselected members are never opened

#### Scenario: late-bound field visible on a streamed member

- **WHEN** a caller iterates `ar.stream_members()`, fully reads a member's stream, then inspects that same member object
- **THEN** any field the backend completed while reading (e.g. `size`/CRC) is now visible on it, because `stream_members()` yields the original object rather than a pre-read copy

#### Scenario: advance releases the previous iterator-owned stream

- **WHEN** a caller advances `stream_members()` after receiving one member stream
- **THEN** the prior stream is closed/invalidated before the next pair is yielded

#### Scenario: random open cannot overlap a streaming pass

- **WHEN** a `stream_members()` pass is active and a random `open()` is attempted
- **THEN** `ArchiveyUsageError` is raised and the active pass remains usable

#### Scenario: abandoned streaming generator releases ownership

- **WHEN** a caller explicitly closes or abandons a partially consumed `stream_members()`
  generator
- **THEN** its current yielded stream is closed and its child/root operation scopes are
  released exactly once

#### Scenario: random open on a solid member re-decompresses with a warning

- **WHEN** `ar.open(member)` is called for a member inside a solid block
- **THEN** the block is re-decompressed from its start and skipped to the member (no persistent decompressed cache is retained)
- **AND** a diagnostic/warning is emitted suggesting `stream_members()` for sequential passes

### Requirement: Transparent link following

`open()` and `read()` SHALL transparently follow symlinks and hardlinks through the shared
reader implementation, and `open()` SHALL preserve its public `ArchiveStream` return type
after following:

```python
def open(self, member: str | ArchiveMember) -> ArchiveStream: ...
```

Hardlinks resolve to the most recent matching target strictly before the link, with the
existing random-access fallback for a malformed later-only source; streaming mode cannot
resolve that forward target. Random-access symlinks resolve to the last matching target
overall, while streaming symlinks can resolve only to a target already seen. Hardlink
targets are archive-root relative; symlink targets are resolved relative to the link's
directory, and absolute/root-escaping targets do not resolve. Bare and trailing-slash
directory forms are both considered.

The reader SHALL follow chains recursively, detect actual cycles by member id rather than
name, and impose no arbitrary depth limit. A missing/unresolvable target raises
`LinkTargetNotFoundError`; a cycle raises `ReadError`. A stream reached through a link
uses the same operation collector/token as the initiating `open()` call rather than
creating or retaining another diagnostic operation.

The fully dereferenced target (the terminal member at the end of the link chain — not the
immediate hop — see `archive-data-model`), when known, is also exposed as
`member.link_target_member`.

**Hardlinks resolve positionally**: the target is the most recent occurrence of the
target name **strictly before** the link in archive order (this is the TAR model — every
real tar writer stores the data-bearing entry before the link entries that reference it,
because hardlinks are detected by inode during archiving; RAR5's redirect model is the
same). With duplicate names this matches what a sequential extraction would link
against on disk at the moment the link is written. An archive whose hardlink source
appears **only later** in archive order is malformed but tolerated: in random-access
mode resolution falls back to the later member (and extraction recovers it — see
`format-tar`'s orphan second pass); in streaming mode a forward-pointing hardlink
cannot be resolved in a single pass and fails per `OnError`.

**Symlinks resolve to the last occurrence overall** of the target name (random-access
mode): a symlink is a *name*, resolved at use time, and the final on-disk state of a
duplicated name after sequential extraction is its last occurrence. In streaming mode a
symlink can only resolve to the latest occurrence seen so far; a forward-pointing
symlink stays unresolved (`link_target_member` is `None`). The two modes SHALL agree on
hardlink resolution for the same archive; the symlink forward-visibility difference is
inherent to a single pass and is documented.

**Target-name resolution.** The stored target string is resolved to an archive-namespace
member name before lookup, because the two link kinds store targets in different
namespaces: a **hardlink** target is archive-relative from the root (the linkname is the
source member's own stored path) and is normalized as-is, while a **symlink** target is
a filesystem path relative to the link's *own directory* (`dir/link -> file` means
`dir/file`) and is joined to that directory first. An absolute symlink target, or one
that `..`-escapes the archive root, cannot name a member — it stays unresolved
(`link_target_member` is `None`; opening through it raises `LinkTargetNotFoundError`).
Directory members carry a trailing `/` in their normalized names, so target lookup tries
both the bare and the `/`-suffixed form.

If the link target is not present in the archive, `LinkTargetNotFoundError` (a
`ReadError`/member error) SHALL be raised. Chains SHALL be followed recursively with
**cycle detection** — the set of **member ids** already visited on the current chain is
tracked, and if a member is revisited the library raises a `ReadError` whose message
reports the cycle. Tracking is by member id, never by name: a chain passing through two
*distinct* members that share a name is not a cycle, and a name-based visited set would
falsely report one. There is no fixed depth limit; an acyclic chain of any length
resolves, and only an actual cycle (or a missing target) fails.

#### Scenario: linked open preserves stream diagnostics

- **WHEN** `reader.open(link_member)` follows a valid chain to file data
- **THEN** it returns one `ArchiveStream` whose operation-filtered diagnostics cover work performed while following and reading that open operation

#### Scenario: opening a hardlink returns the target's data

- **WHEN** `ar.open(hardlink_member)` is called and the hardlink resolves to a file member present earlier in the archive
- **THEN** the returned stream yields that file member's uncompressed data

#### Scenario: missing link target raises LinkTargetNotFoundError

- **WHEN** `ar.open(link_member)` is called and the stored target is absent from the archive
- **THEN** `LinkTargetNotFoundError` is raised

#### Scenario: link cycle is detected by member id

- **WHEN** following a symlink/hardlink chain revisits a member already on the current chain
- **THEN** `ReadError` is raised reporting the cycle (no infinite recursion)

#### Scenario: reading via a symlink member

- **WHEN** `ar.open(symlink_member)` is called and the symlink resolves to a file member in the archive
- **THEN** the returned stream yields that file member's uncompressed data

#### Scenario: relative symlink target resolves against the link's directory

- **WHEN** a symlink at `dir/link` has stored target `file` (or `./file`)
- **THEN** resolution looks up `dir/file` in the archive namespace (joined to the link's directory), not a root-relative `file`

#### Scenario: absolute symlink target stays unresolved

- **WHEN** a symlink's stored target is absolute or `..`-escapes the archive root
- **THEN** `link_target_member` remains `None` and opening through it raises `LinkTargetNotFoundError`

#### Scenario: hardlink resolves to an earlier member

- **WHEN** `ar.open(hardlink_member)` is called and a matching file name appears strictly before the hardlink
- **THEN** the returned stream yields that earlier file member's data

#### Scenario: duplicate names — hardlink links to the latest earlier occurrence

- **WHEN** multiple members share the hardlink's target name and at least one precedes the link
- **THEN** hardlink resolution selects the most recent occurrence strictly before the link

#### Scenario: duplicate names — symlink resolves to the last occurrence

- **WHEN** multiple members share the symlink's target name in random-access mode
- **THEN** symlink resolution selects the last occurrence overall

#### Scenario: hardlink source only appears later (malformed archive)

- **WHEN** a hardlink's source appears only later in archive order
- **THEN** random-access mode falls back to that later member; streaming mode cannot resolve the forward target

#### Scenario: same-named members on one chain are not a false cycle

- **WHEN** a link chain passes through two distinct members that share a name
- **THEN** cycle detection (by member id) does not treat that as a cycle


### Requirement: Context-manager and close lifecycle

The reader SHALL implement `__enter__`, `__exit__`, and explicit `close()`. Lifecycle state
(`OPEN`, `READER_CLOSED`, `TEARDOWN_RUNNING`, `TEARDOWN_COMPLETE`) and lease count SHALL be
guarded independently from materialization. `ArchiveReader.close()` SHALL be idempotent.
Called without unsupported concurrent operations, it atomically marks `READER_CLOSED`.

Each random-open member stream SHALL own a backend-resource lease. Already-open member
streams remain usable according to their individual capabilities after reader close and keep
the required backend resources alive. Backend/source teardown SHALL occur exactly once after
both the reader is closed and the final member-stream lease is released. A failed open releases its reserved
lease; this includes lazy initialization failure and closing a lazy stream before first use.
The final releaser claims teardown under the lifecycle lock, performs it after releasing that
lock, and records completion without retry. Backend teardown and inner stream close execute
outside lifecycle locks. A lazy-open failure raises its translated error from the triggering
operation, permanently releases/closes that handle, makes later I/O raise normal closed-stream
`ValueError`, and leaves repeated stream `close()` a no-op.

If explicit reader/member close triggers final teardown and teardown fails, the closer SHALL
be irrevocably closed and the translated error SHALL propagate once; repeated closes SHALL
not retry or re-raise it. A safety-net finalizer SHALL use the same once guards, never raise,
and MAY report through `sys.unraisablehook` only outside all Archivey locks. Native
accelerator finalizers retain their close-before-free guarantee.

Member close SHALL release its lease in `finally` even when inner close fails. If inner close
and the resulting final backend teardown both fail, both translated errors SHALL be preserved
in an `ExceptionGroup`. `__exit__` SHALL always call `close()`; a close failure propagates on
normal exit, and during body-exception unwinding the body exception remains available through
normal Python exception chaining.

Archivey SHALL close path handles and wrappers it owns only after the final lease. It SHALL
never close a caller-supplied `BinaryIO`; the caller must keep it open through all reader and
escaped-stream use. If the caller closes it early, a later operation raises
`ArchiveyUsageError` for the closed source; concurrent external close with I/O is
unsupported.

Consequently, exiting `with open_archive(...) as reader` closes the reader but an escaped
member stream intentionally extends backend resource lifetime until that stream closes.
Callers SHOULD close member streams promptly. Under `MemberStreams.CONCURRENT`,
`reader.close()` drains in-flight worker `open()`/`read()` calls (blocks until they
return) before transitioning to closed; escaped idle streams keep their lifecycle leases.
Without `CONCURRENT`, concurrent reader close with an actively executing worker call is
rejected. No close-vs-stream-I/O linearization is promised beyond that draining contract.

After reader close, repeated `close()` / `__exit__` are no-ops and already-open streams
continue according to their capabilities. Every new reader operation or property—including
`__enter__`, iteration/listing/lookup, metadata/cost/source counters, `open`/`read`,
`stream_members`, and extraction—SHALL raise `ArchiveyUsageError`. Escaped streams
use context captured before close for error translation and MUST NOT call those properties.
Their lease-bound short-lived worker tokens prevent final teardown from racing each call.

#### Scenario: escaped member stream survives reader close

- **WHEN** a member stream is opened, then the reader is closed without concurrent I/O
- **THEN** new reader operations raise `ArchiveyUsageError`, while the existing
  stream remains usable until it is closed
- **AND** backend teardown occurs exactly once after that final stream close

#### Scenario: idle lease is not active overlap

- **WHEN** a random-open stream is idle and `reader.close()` runs
- **THEN** close succeeds and releases the reader lease
- **AND** later operations on that stream use its lease-bound worker entry until stream close

#### Scenario: failed eager member open leaks no lifecycle lease

- **WHEN** `_open_member` raises after reserving a resource lease
- **THEN** the reservation is released and a later reader close can complete teardown

#### Scenario: failed lazy member open closes its handle

- **WHEN** first I/O on a lazy member handle makes `_open_member` raise
- **THEN** the translated error is surfaced, its lease is released, later I/O gets normal
  closed-stream `ValueError`, and repeated close is a no-op

#### Scenario: final teardown failure is attempted once

- **WHEN** explicit reader or final-stream close claims teardown and backend close raises
- **THEN** that closer is still irrevocably closed, the translated error propagates once,
  lifecycle reaches `TEARDOWN_COMPLETE`, and repeated close does not retry

#### Scenario: member and teardown close failures are both preserved

- **WHEN** final member close encounters both an inner-close error and backend teardown error
- **THEN** its lease/state are still released exactly once and an `ExceptionGroup` preserves
  both translated failures

#### Scenario: caller-owned source is never closed by Archivey

- **WHEN** a reader and all escaped streams over a caller-supplied `BinaryIO` are closed
- **THEN** Archivey releases its wrappers but does not call `close()` on that source

#### Scenario: context exit closes the reader

- **WHEN** an `open_archive()` context exits normally or through an exception
- **THEN** the reader is marked closed and its lease is released
- **AND** backend resources are released immediately unless an escaped member stream still
  owns a lease

---

### Requirement: Password candidates and provider

`password` SHALL accept, besides a single `str | bytes` value:

- an **ordered sequence** of candidate values, and/or
- a **provider callable** `PasswordProvider = Callable[[PasswordRequest], str | bytes | None]`, where

```python
@dataclass(frozen=True)
class PasswordRequest:
    member: ArchiveMember | None  # the member being decrypted; None for archive-level
                                  # (header) decryption, where no member exists yet
    attempt: int                  # 1 on the first ask for this unit; increments when a
                                  # previously returned password failed for it
```

For each encrypted unit (an encrypted member, a 7z folder, or an encrypted archive
header), the reader SHALL try, in order: the per-archive **known-good list** (passwords
that already succeeded during this open, most recent first), then the remaining sequence
candidates, then — when a provider is given — the provider, repeatedly, until it returns
`None`. The provider receives a `PasswordRequest` carrying the `ArchiveMember` being
decrypted (so an interactive caller can present which entry is being asked about, or
`None` for archive-level decryption — a header-encrypted 7z/RAR5, where no member exists
yet) and the `attempt` count for the unit (so a retry after a wrong password is
distinguishable from a first ask). The context object exists so future fields (e.g. the
prior error) can be added without breaking provider implementations — a bare callable
parameter could not be widened compatibly. Every password that succeeds SHALL be added
to the known-good list for the remainder of the operation, so a provider is consulted
once per *new* password rather than once per member, and a single forward streaming pass
stays viable on archives whose members use different passwords. When all candidates are
exhausted (or the provider returns `None`) for a unit that needs one, the reader SHALL
raise `EncryptionError`. There is no per-call password parameter on `open()`/`read()` —
the candidate model subsumes it.

This existing order SHALL be safe for concurrent post-materialization opens. Static
candidates are immutable and ordered. Known-good snapshots/promotions and per-unit
tried/attempt state are synchronized; expensive key derivation/decryption is performed
without lifecycle/operation, materialization, or password-state locks, but MAY use a required
backend/source lock around an atomic decode/handle operation.

At most one provider-driven resolution turn may be active per reader. Provider invocation
SHALL use a claim/call/validate/publish protocol: claim the turn under a condition, release
all Archivey locks, call the provider, then test returned candidates for that encrypted unit
without lifecycle/materialization/password locks (using a required backend/source lock only
for atomic validation work). It then publishes the validated outcome, releases the turn, and
wakes waiters in a `finally` path. The turn remains claimed through repeated provider attempts
until success or `None`. A waiter SHALL recheck known-good passwords before claiming the next
turn, avoiding a duplicate prompt because a successful result is promoted before wake-up.
Provider callbacks are therefore serialized and always lock-free. Reentrant provider code
that starts another password-requiring operation on the same reader SHALL raise
`ArchiveyUsageError` rather than deadlocking. Attempt counts remain per encrypted
unit.

#### Scenario: sequence of candidates across differently-encrypted members

- **WHEN** an archive whose members are encrypted with two different passwords is opened with `password=[pw_a, pw_b]` and iterated in one streaming pass
- **THEN** every member decrypts using whichever candidate matches its unit, and the pass completes without random access

#### Scenario: provider is consulted and its answer is reused

- **WHEN** a provider callable is given and a member needs a password not yet known
- **THEN** the provider is called with a `PasswordRequest` carrying that `ArchiveMember`; a returned password that succeeds is added to the known-good list and later members encrypted with it do not trigger further provider calls

#### Scenario: provider sees the retry count

- **WHEN** a provider's returned password fails to decrypt the unit and the provider is consulted again
- **THEN** the new `PasswordRequest` carries an incremented `attempt`, so an interactive caller can display "wrong password, try again"

#### Scenario: provider gives up

- **WHEN** the provider returns `None` for a unit no known candidate decrypts
- **THEN** `EncryptionError` is raised for that unit

#### Scenario: header decryption passes a memberless request

- **WHEN** a header-encrypted archive is opened with only a provider and the header must be decrypted to list members
- **THEN** the provider is called with a `PasswordRequest` whose `member` is `None` (no member exists yet)

#### Scenario: concurrent encrypted opens share known-good state safely

- **WHEN** workers concurrently open differently encrypted units after materialization
- **THEN** each follows candidate order independently, successful passwords are promoted
  without races/duplicates, and no unit overwrites another's attempt state

#### Scenario: provider callbacks are serialized without internal locks

- **WHEN** two workers need the password provider at once
- **THEN** only one provider callback runs at a time with no Archivey lock held; validation
  holds no lifecycle/materialization/password lock but may use its required backend/source
  lock, and the waiter resumes after the validated outcome is published

#### Scenario: password-provider same-reader reentry fails instead of deadlocking

- **WHEN** a provider callback starts another password-requiring operation on the same reader
- **THEN** that nested operation raises `ArchiveyUsageError`

---

### Requirement: Confirm candidates when a weak check permits retries

When an implemented format has a password check that can admit wrong values, a candidate
SHALL NOT be accepted or added to the per-archive known-good list on that weak check alone
when another distinct candidate may be tried. The backend SHALL first confirm the
candidate with a stronger check — the strongest available signal that can reject a wrong
candidate within bounded work (a bounded decompression prefix, a per-candidate checksum
computed in one shared pass, or full validation when the unit is small). Confirmation
SHALL obey the "Bounded implicit temporary storage" requirement: it SHALL NOT buffer
plaintext proportional to the unit size to memory or temporary storage.

After confirmation, the backend MAY re-open or re-decode the accepted candidate to
produce the caller's stream (formats whose sources are seekable always can). The returned
stream SHALL retain the format's ordinary read-time integrity checking, so
bounded confirmation never weakens the read-time contract relative to the
single-candidate path: data that is wrong beyond what confirmation examined still fails
on the caller's read exactly as it would have with a single password.

“Another candidate may be tried” includes two or more distinct known-good/static values
and a provider that can return another answer after failure. A provider SHALL remain lazy;
the reader SHALL NOT enumerate it in advance or assume it is finite. Duplicate values do
not create another distinct candidate. An `EncryptionError` raised by the provider
callback itself is a provider failure, not a candidate decrypt result; it SHALL propagate
without being rewritten as candidate exhaustion or password/corruption ambiguity.

If confirmation fails after a weak check and all candidates are exhausted, the result can
be intrinsically ambiguous: the candidate may be wrong, or it may be correct and the
encrypted unit corrupt. The reader SHALL describe both possibilities rather than promise
an impossible classification. It MAY use `EncryptionError` for this candidate-exhaustion
state. It SHALL NOT return an unvalidated candidate based on order, heuristics, or a
warning.

A single distinct static candidate MAY retain the format's normal lazy streaming path;
read-time integrity failures on that path retain the format's ordinary corruption/error
translation. This requirement does not assign check strength or authentication behavior
to formats whose readers are not implemented.

#### Scenario: a wrong candidate that passes a weak per-open check does not shadow the right one

- **WHEN** an encrypted member is opened with two candidate passwords and the wrong one, tried first, happens to pass the format's weak per-open check
- **THEN** the reader rejects the wrong candidate through confirmation and returns a stream opened with the correct candidate

#### Scenario: confirmation is bounded

- **WHEN** an encrypted member far larger than the confirmation bound is opened with multiple candidates
- **THEN** candidate confirmation completes without buffering plaintext proportional to the member size to memory or temporary storage

#### Scenario: provider remains lazy but retryable

- **WHEN** a provider's first answer passes a weak check but fails confirmation
- **THEN** the reader requests the provider's next answer without pre-enumerating it, and accepts an answer only after confirmation

#### Scenario: provider failure is not candidate exhaustion

- **WHEN** a candidate fails confirmation and the provider callback subsequently raises its own `EncryptionError`
- **THEN** that provider exception propagates unchanged rather than being replaced by the candidate-exhaustion ambiguity error

#### Scenario: exhausted confirmation reports the irreducible ambiguity

- **WHEN** one or more candidates pass a weak check but fail confirmation and no candidate succeeds
- **THEN** the failure states that the passwords may be wrong or the encrypted unit may be corrupt, and no candidate's bytes are returned

#### Scenario: one distinct static candidate retains lazy streaming

- **WHEN** the password input contains one distinct static value, including duplicate copies of that value
- **THEN** the member is not eagerly consumed solely for candidate disambiguation, and ordinary read-time error translation applies

### Requirement: Bounded implicit temporary storage

Reader operations SHALL NOT consume memory or temporary storage proportional to a
member's or the archive's size as an implicit side effect of opening, reading, validating,
or password-confirming a member. Silently spooling member plaintext to a temporary file —
however bounded in RAM — is such a side effect and is not permitted: a caller who opens a
member has consented to streaming reads, not to a hidden on-disk copy of the member.

A per-format materialization strategy that inherently requires proportional temporary
storage (for example `format-rar`'s documented `unrar x`-to-temporary-directory serving
strategy) is permitted only when it is explicitly declared in that format's capability
spec; such strategies are format-level documented behavior, not implicit side effects.
This requirement does not restrict the caller's own buffering of a returned stream.

#### Scenario: opening an encrypted member with many candidates stays bounded

- **WHEN** an encrypted member of arbitrary size is opened with multiple password candidates
- **THEN** candidate confirmation uses temporary memory/storage bounded by a constant, not by the member size

#### Scenario: proportional strategies must be declared per format

- **WHEN** a backend can only serve member data by materializing it (e.g. an external-binary extraction strategy)
- **THEN** that strategy is declared in the format's capability spec rather than adopted silently by a reader operation

---

### Requirement: Explicit configuration object

The system SHALL define these complete frozen configuration schemas:

```python
@dataclass(frozen=True)
class ExtractionLimits:
    max_extracted_bytes: int | None = 2 * 2**30
    max_ratio: float | None = 1000.0
    ratio_activation_threshold: int = 5 * 2**20
    max_entries: int | None = 1_048_576

    UNLIMITED: ClassVar["ExtractionLimits"]

@dataclass(frozen=True)
class ArchiveyConfig:
    use_rapidgzip: AcceleratorMode = AcceleratorMode.AUTO
    use_indexed_bzip2: AcceleratorMode = AcceleratorMode.AUTO
    strict_archive_eof: bool = False
    extraction_limits: ExtractionLimits = ExtractionLimits()
    diagnostic_policy: DiagnosticPolicy = DiagnosticPolicy()
    max_retained_diagnostic_references: int = 256
    on_diagnostic: Callable[[Diagnostic], None] | None = None
```

`max_retained_diagnostic_references` SHALL be non-negative. Policy/default/override
mappings and both config dataclasses SHALL be defensively immutable. `config=None`
selects the immutable library default. Configuration is explicit only; Archivey SHALL
read no mutable global/context-local diagnostic policy or callback.

A reader carries its open config. A later `extract_all(config=...)` MAY override policy,
callback, strictness, accelerators, and limits for new work, but its
`max_retained_diagnostic_references` field SHALL NOT replace, reset, lower, or enlarge the
existing reader collector's budget. Per-call `limits` still takes precedence over
`config.extraction_limits`, then the reader/library default. Existing per-call operational
arguments remain outside `ArchiveyConfig`.

`strict_archive_eof=False` follows ordinary diagnostic policy for a failed EOF check.
`True` forces `TruncatedError` after the ordered diagnostic counting/delivery rules
specified by `error-handling`.

Callbacks run synchronously after count/retention/logging updates and without any
collector, reader, stream, backend, or registry lock. Snapshot reads from a callback are
allowed. Starting another operation on the same currently emitting reader/stream SHALL
raise `UnsupportedOperationError`; operating on a different reader is allowed.

#### Scenario: complete default configuration

- **WHEN** `ArchiveyConfig()` is used
- **THEN** accelerators are AUTO, EOF strictness is false, extraction limits are the documented defaults, diagnostics default to COLLECT, the budget is 256, and no callback is installed

#### Scenario: extraction override cannot replace lifetime budget

- **WHEN** a reader opened with budget 10 calls `extract_all(config=ArchiveyConfig(max_retained_diagnostic_references=1000))`
- **THEN** new policy/callback settings may apply, but all reader-owned diagnostics remain subject to the original budget 10

#### Scenario: extraction limits travel in the config

- **WHEN** `archivey.extract(src, dest, config=ArchiveyConfig(extraction_limits=ExtractionLimits(max_ratio=100)))` runs
- **THEN** the 100:1 per-member ratio limit is enforced (see `safe-extraction`)

### Requirement: Reader-lifetime cumulative diagnostic snapshots

Every successfully created `ArchiveReader` SHALL own a diagnostic collector for its
lifetime and expose:

```python
@property
def diagnostics(self) -> DiagnosticSummary: ...
```

Each access SHALL return a fresh immutable cumulative snapshot. Exact counts SHALL include
automatic-detection occurrences that led to the reader plus every open/list/read/stream/
extract occurrence subsequently owned by it, including events whose detail could not be
retained. Previously returned snapshots SHALL not change.

A stream returned by a reader SHALL expose an operation-filtered `diagnostics` snapshot
over the same collector. It SHALL not separately retain aggregate copies merely to serve
both stream and reader views.

#### Scenario: reader snapshot grows over its lifetime

- **WHEN** a reader is opened after a detection conflict, then listing emits a scan diagnostic and a member stream emits a rewind diagnostic
- **THEN** a later `reader.diagnostics` has exact cumulative counts for all three in emission order, while an earlier snapshot remains unchanged

#### Scenario: stream view is a filtered reader view

- **WHEN** two member streams emit different diagnostics
- **THEN** each stream's snapshot includes only its operation's events and the reader snapshot includes both, without separately retained aggregate copies

#### Scenario: callback may query but not re-enter

- **WHEN** `on_diagnostic` reads `reader.diagnostics` and then attempts `reader.read(...)` on the same emitting reader
- **THEN** the snapshot read succeeds and includes the current event, while the operational reentry raises `UnsupportedOperationError`

### Requirement: Collection form of MemberSelector

`MemberSelector` SHALL accept, besides a predicate, a `Collection[str | ArchiveMember]`,
normalized to a predicate at the API boundary:

- a `str` entry matches **every** member whose normalized name equals it — duplicate
  names all match (extraction of duplicate selected names keeps sequential
  last-wins-on-disk semantics);
- an `ArchiveMember` entry matches by **identity** (`archive_id` + `member_id`;
  members are unhashable, so normalization builds an id set, never a member set);
- string and member entries MAY be mixed in one collection.

#### Scenario: name entry selects all duplicates

- **WHEN** `stream_members(members=["a.txt"])` runs on an archive containing two members named `a.txt`
- **THEN** both are yielded, in archive order

#### Scenario: member entry selects by identity

- **WHEN** a specific `ArchiveMember` (one of two duplicates) is passed in the collection
- **THEN** only that member is selected, not its same-named sibling

---

### Requirement: Multiple concurrently-open member streams

Every **random-access** reader (`streaming=False`) opened with
`MemberStreams.CONCURRENT` SHALL support any number of member streams opened from that
single reader being held open simultaneously and read in interleaved order without
corrupting one another. Without that declared capability the single-live-stream default
contract applies ("Declared member-stream capabilities"); with it, access cost still
does not determine legality.

**Post-materialization worker seam.** After one owner has completed `members()` or
`scan_members()` and the reader has published its member list/name index, concurrent calls
from multiple threads to `open(member_or_name)` SHALL be supported. Streams returned by
different opens SHALL have independent logical positions/state: workers MAY concurrently call
`read`, `readinto`, and `close` on **different stream objects**, plus `seek`/`tell` under
`MemberStreams.SEEKABLE` where that stream supports positioning. Non-seekable streams
retain normal `BinaryIO` behavior:
`seekable()` is false and unsupported positioning raises `io.UnsupportedOperation`.
Simultaneous operations on the same stream object require caller synchronization, matching
ordinary Python file objects. The supported behavior SHALL NOT rely on the GIL.

**Materialization boundary.** The members and name index SHALL be built in private/local
state, completed (including link resolution), and published together exactly once as immutable
internal containers. A public API whose existing return type is `list` SHALL return a
copy that cannot structurally mutate those cache containers. `ArchiveMember` objects
retain their existing backend-populated late-bound fields. Under `MemberStreams.CONCURRENT`,
overlapping first-touch materialization is coordinated: exactly one caller builds while
others wait for the published snapshot (or a failed attempt returns to `UNMATERIALIZED`
and wakes waiters to re-elect); no caller observes a partial cache. Without `CONCURRENT`,
a second overlapping materialization SHALL raise `ArchiveyUsageError`. Distinct
reader-wide passes remain single-owner. Late-bound random-open updates
to a member MUST be idempotent and synchronized; conflicting last-writer-wins updates are
forbidden. Materialization state is exactly `UNMATERIALIZED` / `MATERIALIZING` /
`MATERIALIZED`; reader lifecycle is separate and MUST NOT add `CLOSED` to the cache state.
A failed build discards private state and returns to `UNMATERIALIZED`.

**Backend compliance.** Archivey-owned byte-range backends MUST use views with per-view
positions and atomic shared-source handle operations. External-library backends MUST provide
equivalent coordination: ZIP MAY rely on stdlib `_SharedFile` for seek/read and MUST
serialize `ZipFile.open` / member-stream close / `ZipFile.close` under
`MemberStreams.CONCURRENT` so free-threaded `_fileRefCnt` updates cannot race;
random-access TAR and ISO MUST
use the one-per-reader lock specified by `tar-concurrent-open`, covering every operation on
the shared library handle. A solid format satisfies correctness by giving each returned
stream independent logical position/state. It MAY use per-open decoders or a synchronized,
bounded/spooled shared decode/materialization strategy; the contract neither requires
one decoder per open nor promises elimination of redundant decompression.

**Reader-wide operation ownership.** Distinct reader-wide passes (`__iter__`,
`stream_members`, `extract_all`) and `scan_members` / `get_members_if_available`
initialization remain single-owner and cannot overlap one another or the random worker
seam. Under `MemberStreams.CONCURRENT`, first-touch materialization is coordinated
(wait/share) and `reader.close()` drains in-flight worker calls rather than rejecting
them. The base reader SHALL represent ownership with an explicit unforgeable root token, not thread
identity. Private helpers MAY receive that token to enter child scopes: materialization may
perform link-data reads; a random worker `open()` may do name lookup/link following and late
link-data reads; `extract_all` may inspect available members/source counters and drive one or
more `stream_members` passes; and a pass may advance and perform I/O/close on its yielded
stream. An unrelated/reentrant public call has no token even on the owner thread and is
rejected. The later conflicting operation SHALL raise `ArchiveyUsageError` before
changing state; the earlier root and children remain usable.

Random `open()` and each operation on a random-open stream SHALL hold a short-lived worker
token only while that call executes. An idle open stream owns a lifecycle lease, not active
operation ownership. It carries a private lease-bound entry capability so later stream I/O
remains admissible after `reader.close()`. Under `CONCURRENT`, `close()` waits for
in-flight worker tokens to drain, then closes; without `CONCURRENT`, close is rejected while
a worker call is executing. Closure does not enable any new reader API.

"Overlap" means concurrent method/I/O execution, not the lifetime of an idle open member
stream: a non-concurrent `reader.close()` MAY run while member streams remain open, and their
leases preserve resources for later stream I/O. This is not a blanket thread-safety guarantee
for every reader method.

**`stream_members()` is separate.** A `stream_members()` pass owns the reader's one-pass
data path. It MUST NOT overlap random `open()` work or any other forward/data pass.
Advancing the iterator closes/invalidates the previously yielded stream before yielding the
next; this iterator-owned lifecycle does not apply to independent streams returned by random
`open()`. The yielded stream carries a child scope so its I/O is permitted during the pass.
Exhaustion, exceptions, explicit generator close, and generator abandonment/finalization
SHALL close the current yielded stream and release the pass scope/token exactly once. A caller
needing simultaneous streams SHALL materialize and use random `open()`.

**Cost is informational.** `AccessCost.SOLID` / `solid_block_count` tell callers that
simultaneous random streams may repeat decompression. They never disable the guarantee.
`stream_members()` remains the efficient bounded-memory, one-decode path for a sequential
solid-archive workload.

**Detectable closed-source misuse and bounds.** A live lease prevents reader-owned backend
resources from closing underneath a member stream. If a caller-owned source is nevertheless
closed externally, the reader surface SHALL raise a typed error rather than return arbitrary
or empty bytes. Archivey shared-source views clamp bounds extending past the available source
like normal streams, so truncation produces a short view rather than a construction failure.

**Callbacks and lock scope.** Password providers, selectors/filters, progress callbacks,
logging handlers, diagnostic formatting/stamping, `sys.unraisablehook`, and user-visible
close/finalizer hooks MUST execute without any Archivey lock held. Decode/password candidate
validation is not a callback: it MUST run without lifecycle/operation, materialization, or
password locks, but MAY hold the narrowly required backend/source lock around an atomic
decoder/handle operation. Nested reader-state order is lifecycle/operation → materialization
→ password. Backend/source locks are leaves. Individual stream state uses claim/call/publish
and MUST be released before invoking lazy `open_fn`, inner I/O/close, backend/source
operations, or lifecycle lease release.

#### Scenario: workers open and operate on independent streams after materialization

- **WHEN** a random-access reader has completed `members()` and two threads concurrently
  call `open()` for different members, then use the operations each stream supports
- **THEN** each stream returns exactly its member's bytes and keeps its own position, with no
  cache, reader-state, or source-position race

#### Scenario: declared concurrency is honored regardless of cost

- **WHEN** a reader opened with `MemberStreams.CONCURRENT` opens two member streams and
  reads them interleaved
- **THEN** both are correct on every format; `AccessCost` describes the expense but never
  denies the declared capability

#### Scenario: concurrent first-touch materialization is coordinated

- **WHEN** several threads call `open()`, `members()`, or `get()` as first operations on
  an un-materialized `CONCURRENT` reader
- **THEN** exactly one thread performs materialization while the others wait, every
  waiting thread proceeds against the published snapshot, and no thread receives
  `ArchiveyUsageError` for the overlap

#### Scenario: materialization failure does not close or poison the cache

- **WHEN** a materialization owner fails before publication
- **THEN** its private structures are discarded, cache state returns to `UNMATERIALIZED`,
  waiters are woken to re-elect or observe the error, and lifecycle remains independently
  `OPEN`

#### Scenario: distinct passes do not overlap the worker seam

- **WHEN** worker member-stream operations are active and `__iter__`, `stream_members`,
  or `extract_all` is attempted concurrently
- **THEN** the detected later operation raises `ArchiveyUsageError` without closing
  or corrupting the active member streams

#### Scenario: concurrent close drains workers then closes

- **WHEN** `reader.close()` is called under `CONCURRENT` while worker `open()`/`read()`
  calls are executing
- **THEN** `close()` blocks until those calls return, transitions to closed, and does not
  raise merely because workers were active; escaped idle streams remain leased

#### Scenario: solid concurrent streams are correct but may repeat work

- **WHEN** two members in one solid block are opened simultaneously
- **THEN** each stream returns correct independent bytes; `AccessCost.SOLID` describes the
  possible re-decode cost, and no concurrency exception is raised

#### Scenario: free-threaded correctness does not depend on the GIL

- **WHEN** the post-materialization worker scenario runs for a backend/runtime combination
  covered by the required CPython `3.13t` job
- **THEN** cache publication, lifecycle leases, password state, and member/source positions
  remain data-race-free with the same observable results as a regular build

### Requirement: Coordinated first-touch materialization

A reader opened with `MemberStreams.CONCURRENT` SHALL coordinate concurrent first-touch
operations on a not-yet-materialized member list by blocking all but one caller until the
immutable snapshot is published, rather than rejecting the overlap. Materialization SHALL
run exactly once, and the non-concurrent and uncontended paths SHALL be unchanged.

#### Scenario: concurrent first-touch converges on one materialization

- **WHEN** several threads call `open()`, `members()`, or `get()` simultaneously as the
  first operations on an un-materialized `CONCURRENT` reader
- **THEN** exactly one thread performs materialization while the others block on a
  condition, and once the immutable snapshot is published every waiting thread proceeds
  against it with no thread receiving `ArchiveyUsageError` for the overlap

#### Scenario: failed first-touch wakes every waiter without a partial snapshot

- **WHEN** the electing thread's first-touch materialization fails (for example a corrupt
  header) while other threads are blocked waiting
- **THEN** the cache returns to the un-materialized state, no partial snapshot is ever
  observed, and each waiting thread either observes the same translated error or cleanly
  re-elects a fresh attempt

#### Scenario: uncontended and default paths are unchanged

- **WHEN** materialization happens on a default (non-`CONCURRENT`) reader or with no
  contention
- **THEN** no waiting is introduced, and the member scan, link reads, and callbacks still
  run with no reader-state lock held

### Requirement: Draining reader close

Under `MemberStreams.CONCURRENT`, `reader.close()` SHALL wait for in-flight worker
`open()`/`read()` calls to return and then transition the reader to closed, rather than
raising because workers are active. Escaped open member streams SHALL remain governed by
the existing lifecycle-lease contract, and close idempotency, one-shot teardown, and
post-close rejection SHALL be preserved.

#### Scenario: close drains in-flight worker calls

- **WHEN** a thread calls `reader.close()` while one or more worker `open()`/`read()` calls
  are executing on other threads
- **THEN** `close()` blocks until those calls return, then transitions the reader to
  closed, and does not raise `ArchiveyUsageError` merely because workers were active

#### Scenario: escaped stream survives a drained close

- **WHEN** a member stream that escaped the reader is still open as `close()` returns
- **THEN** it remains readable under the lifecycle-lease contract until its own `close()`,
  and archive teardown runs exactly once after the final lease is released

#### Scenario: concurrent double close is idempotent

- **WHEN** two threads call `reader.close()` (or `__exit__`) simultaneously
- **THEN** teardown runs exactly once, both calls return without error, and simultaneous
  inner-close and teardown failures surface once as an `ExceptionGroup`

#### Scenario: operations after close still reject

- **WHEN** a new `open()` or other reader operation is attempted after `close()` returned
- **THEN** it raises `ArchiveyUsageError` (post-close), unchanged from today

### Requirement: Distinct passes and shared streams remain single-owner

Overlapping *distinct* reader-wide passes or concurrent access to a single stream object
SHALL remain rejected or caller-synchronized, so the coordinated contract stays bounded to
materialization and close.

#### Scenario: a different reader-wide pass is still rejected

- **WHEN** a reader is running `extract_all()` or an active `stream_members()` pass and
  another thread starts a different pass (`__iter__`, `stream_members()`, or
  `extract_all()`)
- **THEN** the later operation is rejected with `ArchiveyUsageError`, unchanged

#### Scenario: same-stream access stays the caller's responsibility

- **WHEN** two threads call `read`/`readinto`/`seek`/`close` on the same `ArchiveStream`
  object concurrently
- **THEN** correctness is the caller's responsibility under standard file semantics; this
  change adds no per-stream locking

#### Scenario: unsupported seek keeps normal stream semantics

- **WHEN** a returned member stream reports `seekable() is False` and the caller seeks
- **THEN** it raises `io.UnsupportedOperation` rather than gaining a synthetic seek guarantee

#### Scenario: extraction is an owner with permitted child passes

- **WHEN** `extract_all()` drives `stream_members()`, reads/closes its yielded streams, and
  performs a random-access hardlink recovery pass
- **THEN** those token-bearing child scopes are permitted while an unrelated public operation
  is rejected

#### Scenario: externally closed source fails loudly

- **WHEN** a caller-owned underlying source is externally closed while a member stream still
  holds a reader lease
- **THEN** the reader surface raises a typed error rather than returning arbitrary or empty
  bytes

### Requirement: Random-access member-open is reentrant and reader-state-free

For every random-access backend, `_open_member` SHALL derive the returned stream from the
member plus immutable/published archive state and coordinated backend resources. It MUST
NOT keep unsynchronized per-open scratch on the reader that another open can overwrite.
Synchronized shared bookkeeping—operation state, stream leases, password/key caches, and
backend handle locks—is permitted and required where applicable.

Archivey-owned byte ranges MUST use shared-source views with per-view position. A
library-owned seek-before-read backend (random-access TAR/ISO) MUST coordinate the complete
shared-handle lifecycle through its per-reader lock. Immutable member/name structures MAY
be read concurrently after materialization.

Forward-only/streaming passes remain out of scope because they own one progressive decoder
and cannot overlap. There is no random-access TAR/ISO exemption: those backends satisfy the
random-access invariant through their locked library streams.

#### Scenario: one open cannot overwrite another open's state

- **WHEN** two post-materialization `open()` calls execute concurrently
- **THEN** neither stores unsynchronized per-open state on the reader, and both returned
  streams remain correct under interleaving

#### Scenario: TAR and ISO comply through comprehensive handle locking

- **WHEN** a random-access TAR or ISO reader opens/uses multiple member streams
- **THEN** every required shared-handle/library decode operation is serialized by its one
  per-reader lock, while archivey callbacks and diagnostics run with no Archivey lock

