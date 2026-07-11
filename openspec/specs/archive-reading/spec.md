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

It yields original caller-read-only members in archive order and an `ArchiveStream` for
file data (`None` for non-files), with peak memory bounded by decompressor state and one
in-flight chunk rather than a whole solid block. A yielded stream is valid only until the
iterator advances. Streams open lazily; selector-excluded and unread members are not
opened. `members` keeps the existing name/member collection or predicate semantics.
There is no transform `MemberFilter` on this pure generator because late-bound member
metadata must continue to update the original member in place.

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

#### Scenario: stream is invalid after advance

- **WHEN** the iterator advances to the next `(member, stream)` pair
- **THEN** the previously yielded stream MUST NOT be used; it is no longer guaranteed to be valid

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

The system SHALL implement the context-manager protocol on `ArchiveReader` so that resources are released when the `with` block exits. A `close()` method SHALL also be available for explicit resource release.

```python
def __enter__(self) -> ArchiveReader: ...
def __exit__(self, *_) -> None: ...
def close(self) -> None: ...
```

After `close()` is called, the reader's behavior is undefined; callers MUST NOT use a closed reader.

#### Scenario: context manager releases resources

- **WHEN** `with archivey.open_archive("archive.zip") as ar:` exits (normally or via exception)
- **THEN** all backend resources (file handles, temp directories, caches) are released

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
