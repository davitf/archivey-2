# Archive Reading

## Purpose

Provides a uniform interface for opening and reading archives across all supported formats. The `ArchiveReader` class presents ZIP, TAR, RAR, 7z, ISO, plain directories, and single-file compressed streams as interchangeable objects with consistent metadata, iteration, and data-access semantics.

## Requirements

### Requirement: Opening an archive for reading

The system SHALL expose a top-level `archivey.open_archive()` function that accepts a file path, `Path`, or binary stream and returns an `ArchiveReader`.

```python
archivey.open_archive(
    source: str | Path | BinaryIO | Sequence[str | Path | BinaryIO],
    *,
    format: ArchiveFormat | None = None,  # override detection
    streaming: bool = False,             # False = random access; True = forward-only one pass
    password: str | bytes | Sequence[str | bytes] | PasswordProvider | None = None,
    encoding: str | None = None,         # None = auto-detect member-name encoding
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

#### Scenario: open with auto-detected format

- **WHEN** `archivey.open_archive("archive.tar.gz")` is called with no `format` override
- **THEN** the library detects the format from magic bytes and returns an `ArchiveReader` wrapping the appropriate backend

#### Scenario: open with explicit format override

- **WHEN** `archivey.open_archive(source, format=ArchiveFormat.ZIP)` is called
- **THEN** the library uses the specified format backend without running detection

#### Scenario: open with password

- **WHEN** `archivey.open_archive(source, password="secret")` is called
- **THEN** the returned `ArchiveReader` uses the provided password for encrypted members

---

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

The system SHALL provide two data-access methods: `read()` which returns the full member content as `bytes`, and `open()` which returns a streaming `BinaryIO` that the caller is responsible for closing.

```python
def read(self, member: str | ArchiveMember) -> bytes: ...
def open(self, member: str | ArchiveMember) -> BinaryIO: ...   # streaming; caller must close
```

Both methods accept either a member name string or an `ArchiveMember` object. An unknown
name raises `KeyError`; an `ArchiveMember` object that was **not yielded by this reader**
raises `ValueError` — the same identity rule as `member in reader`. (Without the check, a
foreign member would resolve against this reader's offsets/paths and could silently
return the wrong data.)

**Integrity on full reads.** When a member carries a verifiable digest, reading it fully
verifies the content (see `compressed-streams`). A mismatch is surfaced **after** all
bytes are delivered: in a streaming `open()` loop the data chunks all arrive normally and
the terminal end-of-stream read raises `CorruptionError`, so the caller never loses a
trailing chunk; `read()` reads to EOF internally and therefore raises `CorruptionError`
(returning no bytes) on mismatch.

**Memory profile — `read()` is unbounded.** `read(member)` materializes the member's
**entire decompressed payload in memory at once** and returns it as a single `bytes`
object. It is intended for small members — configuration files, manifests, small assets —
whose full content comfortably fits in RAM. For large or untrusted members, callers MUST
use `open()` (a streaming `BinaryIO` read in bounded chunks) or `stream_members()` (bounded
sequential iteration) instead; neither buffers the whole payload. `read()` also performs no
decompression-bomb checks (see `safe-extraction`), so a hostile member can expand without
limit — another reason to prefer `open()`/`stream_members()` for anything not known to be small.

#### Scenario: reading member as bytes

- **WHEN** `ar.read("readme.txt")` is called
- **THEN** the full uncompressed content is returned as `bytes`

#### Scenario: opening a member as a stream

- **WHEN** `ar.open("data.bin")` is called
- **THEN** a `BinaryIO` stream is returned; the caller reads from it and closes it when done

#### Scenario: opening a member from a different reader is rejected

- **WHEN** `ar.open(member)` is called with an `ArchiveMember` yielded by a *different* reader
- **THEN** `ValueError` is raised (never data from the wrong entry)

---

### Requirement: Bounded-memory sequential streaming via stream_members

The system SHALL provide `stream_members()` which yields `(member, stream)` pairs in archive order with bounded memory. Decompression is **always streaming**: a solid block is decompressed progressively as its members are consumed, never buffered whole in advance, so peak memory is the decompressor's working state plus one member's in-flight chunk — not a whole block. The yielded stream is only valid until the iterator advances; callers MUST NOT hold it across yields. For non-file members the stream is `None`.

```python
# Shared vocabulary:
MemberSelector = Collection[ArchiveMember | str] | Callable[[ArchiveMember], bool]
MemberFilter   = Callable[[ArchiveMember], ArchiveMember | None]  # transform/sanitize:
                 #   return a (possibly .replace()'d) member, or None to skip. Used by the
                 #   EXTRACTION/WRITING sinks (extract_all, add_members), NOT here.

def stream_members(
    self,
    members: MemberSelector | None = None,
) -> Iterator[tuple[ArchiveMember, BinaryIO | None]]: ...
```

`members` **selects** which members to yield — a collection of members/names, or a
predicate `Callable[[ArchiveMember], bool]`; `None` yields all. Streams are opened lazily,
so unselected members cost nothing. A consequence: an open-time failure (e.g. a wrong or
missing password for an encrypted member) surfaces on the stream's **first read**, not
while iterating — a caller that only lists names never triggers it.

#### Scenario: skipped members are never opened

- **WHEN** `stream_members(members=...)` iterates past members the selector excludes (or the caller never reads a yielded stream)
- **THEN** those members' data is never opened or decompressed, and an encrypted member's password is never requested for them

`stream_members()` deliberately takes **only the selector**, not a transform/sanitize
`MemberFilter`. It is a pure generator that yields the **original, mutable**
`ArchiveMember` so the backend can keep filling late-bound fields (final `size`/CRC, a
`link_target` stored in the member's data) in place on the object the caller holds. A
`MemberFilter` returns a `.replace()` **copy**; applying it here would yield that copy
while the backend went on updating the original, so the caller's object would never see
the late values. Transformation therefore lives at the sinks that consume the stream —
`extract_all()` (applies it on a transient copy; see `safe-extraction`) and the writer's
`add_members()` — where the original is still available for accurate limits/metadata. A
caller streaming directly can of course apply its own transform per item in the loop.

**Two sequential access patterns — different memory profiles:**

| Pattern | Memory profile | When to use |
|---------|---------------|-------------|
| `for m, f in ar.stream_members()` | Bounded and small — streaming decompression; peak ≈ decompressor state + one in-flight chunk. | Sequential one-pass processing: hashing, conversion, scanning. |
| `for m in ar: ar.open(m)` | Bounded, but re-does work on solid blocks — `open()` on a solid member re-decompresses its block from the block start and skips to the member, logging a warning. No growing cache is ever held. | Random or mixed access on `DIRECT` formats; acceptable on solid formats only for a few members. |

The library MUST NOT hold a growing cache of decompressed block data that is released
only at `close()`. On a solid archive, repeated `open()` calls trade CPU (re-decompression)
for bounded memory, and SHOULD emit a `logging.WARNING` via `archivey.backends` advising
`stream_members()` for full sequential passes. For formats without solid compression
(ZIP, plain `.tar`, single-file `.gz`), both patterns are equally efficient — `open()`
seeks directly to the member with no re-decompression.

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

#### Scenario: random open on a solid member re-decompresses with a warning

- **WHEN** `ar.open(member)` is called for a member inside a solid block
- **THEN** the block is re-decompressed from its start and skipped to the member (no persistent decompressed cache is retained)
- **AND** a `logging.WARNING` is emitted suggesting `stream_members()` for sequential passes

#### Scenario: stream is invalid after advance

- **WHEN** the iterator advances to the next `(member, stream)` pair
- **THEN** the previously yielded stream MUST NOT be used; it is no longer guaranteed to be valid

---

### Requirement: Transparent link following

The system SHALL transparently follow symlinks and hardlinks in `open()` and `read()`. If `member.type` is `SYMLINK` or `HARDLINK`, the call is redirected to the target member. This behavior is format-independent and is implemented once in the `ArchiveReader` ABC. The fully dereferenced target (the terminal member at the end of the link chain — not the immediate hop — see `archive-data-model`), when known, is also exposed as `member.link_target_member`.

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

```python
# Illustrative only — the real code lives in internal/base_reader.py:
#   _open_with_link_follow()  (open()/read() link following)
#   _lookup_link_target()     (target-name resolution; not get(name))
#   _resolve_link() / _register_progressively()  (eager link_target_member fill)
def open(self, member: str | ArchiveMember, _seen: frozenset[int] = frozenset()) -> BinaryIO:
    if isinstance(member, str):
        found = self.get(member)  # name lookup — there is no __getitem__ on the reader
        if found is None:
            raise KeyError(f"Member {member!r} not found")
        member = found
    if member.type in (MemberType.SYMLINK, MemberType.HARDLINK) and member.link_target:
        if member.member_id in _seen:
            raise ReadError(f"Link cycle detected at '{member.name}'")
        target = member.link_target_member or self._lookup_link_target(
            member, self._members_by_name_lists
        )
        if target is None:
            raise LinkTargetNotFoundError(f"Link target '{member.link_target}' not in archive")
        return self.open(target, _seen=_seen | {member.member_id})
    return self._open_member(member)
```

This does not rely on format-level link resolution; format-level resolution (e.g. a RAR5 reader following hardlinks internally) happens at a lower level.

#### Scenario: reading via a symlink member

- **WHEN** `ar.read("data/latest")` is called and `"data/latest"` is a `SYMLINK` pointing to `"data/v1.0/report.txt"`
- **THEN** the content of `"data/v1.0/report.txt"` is returned transparently

#### Scenario: relative symlink target resolves against the link's directory

- **WHEN** member `"dir/link"` is a `SYMLINK` whose stored target is `"file"` and the archive contains both `"dir/file"` and a root-level `"file"`
- **THEN** `ar.read("dir/link")` returns the content of `"dir/file"` (not the root-level `"file"`), and `member.link_target_member` points at `"dir/file"`

#### Scenario: absolute symlink target stays unresolved

- **WHEN** a `SYMLINK` member's stored target is absolute (e.g. `"/etc/passwd"`)
- **THEN** `member.link_target_member` is `None` and `ar.open()` on it raises `LinkTargetNotFoundError`

#### Scenario: hardlink resolves to an earlier member

- **WHEN** a `HARDLINK` member is read and its target is an earlier member in archive order
- **THEN** the earlier member's content is returned, resolved in a single forward pass

#### Scenario: duplicate names — hardlink links to the latest earlier occurrence

- **WHEN** an archive contains `A.txt` (content1), then a `HARDLINK` `L → A.txt`, then a second `A.txt` (content2)
- **THEN** `ar.read("L")` returns content1 in **both** access modes, and extraction links `L` against the content1 inode — matching what a sequential extraction leaves on disk

#### Scenario: duplicate names — symlink resolves to the last occurrence

- **WHEN** an archive contains `A.txt` (content1), a `SYMLINK` `S → A.txt`, then a second `A.txt` (content2), opened in random-access mode
- **THEN** `S.link_target_member` points at the second `A.txt` (the final on-disk state of that name)

#### Scenario: hardlink source only appears later (malformed archive)

- **WHEN** a `HARDLINK` precedes its source in archive order and the archive is opened in random-access mode
- **THEN** the link resolves to the later member and `ar.read()` on it returns that content; in streaming mode the same link fails per `OnError` (a single pass cannot see forward)

#### Scenario: link target not in archive

- **WHEN** `ar.open(link_member)` is called and `link_member.link_target` is absent from the archive
- **THEN** `LinkTargetNotFoundError` is raised

#### Scenario: link cycle detected

- **WHEN** following a link chain revisits a member (by member id) already seen on that chain
- **THEN** `ReadError` is raised with a message reporting the cycle (no fixed depth limit is used; only genuine cycles fail)

#### Scenario: same-named members on one chain are not a false cycle

- **WHEN** a link chain passes through two distinct members that share a normalized name
- **THEN** the chain resolves normally — cycle detection tracks member ids, so the shared name does not trigger a spurious cycle error

---

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

### Requirement: Explicit configuration object

The system SHALL define a frozen `ArchiveyConfig` dataclass carrying the library's
tuning/policy knobs, passed explicitly as `config=` to `open_archive()` and
`extract()` (`None` selects the immutable library default):

```python
@dataclass(frozen=True)
class ExtractionLimits:
    # None disables that guard; the UNLIMITED preset is all-None.
    max_extracted_bytes: int | None = 2 * 2**30
    max_ratio: float | None = 1000.0
    ratio_activation_threshold: int = 5 * 2**20
    max_entries: int | None = 1_048_576

    UNLIMITED: ClassVar["ExtractionLimits"]  # every guard disabled (trusted archives)

@dataclass(frozen=True)
class ArchiveyConfig:
    use_rapidgzip: AcceleratorMode = AcceleratorMode.AUTO
    use_indexed_bzip2: AcceleratorMode = AcceleratorMode.AUTO
    strict_archive_eof: bool = False
    extraction_limits: ExtractionLimits = ExtractionLimits()
```

A reader carries the config it was opened with; `extract_all()` uses the reader's
config unless the call overrides it. The extraction limits are additionally overridable
per call via `limits: ExtractionLimits | None` on `extract()`/`extract_all()`
(precedence: per-call `limits` > `config.extraction_limits` > library default; the
`ExtractionLimits.UNLIMITED` preset disables all four guards — see `safe-extraction`).
Configuration is **explicit only**: the library
SHALL NOT read ambient state (no context variables, no mutable global default) to
resolve configuration. Per-call operational arguments — `format`, `streaming`,
`password`, `encoding`, and extraction's `members`/`filter`/`policy`/`overwrite`/
`on_error`/`on_progress` — are keyword parameters and MUST NOT be absorbed into the
config object.

`strict_archive_eof` governs archive-level end-of-data verification (today: the TAR
two-block trailer check; extensible to other formats): `False` (default) emits a
`logging.WARNING` on a failed check, `True` raises `TruncatedError`. The check
necessarily runs only after a full pass reaches the archive's end.

#### Scenario: default configuration without a config argument

- **WHEN** `archivey.open_archive(source)` is called with no `config`
- **THEN** the library default `ArchiveyConfig()` applies (accelerators AUTO, `strict_archive_eof=False`, default limits)

#### Scenario: strict end-of-archive via config

- **WHEN** a truncated TAR (missing trailer) is fully read under `config=ArchiveyConfig(strict_archive_eof=True)`
- **THEN** `TruncatedError` is raised at the end of the pass; with the default config the same condition only logs a warning

#### Scenario: extraction limits travel in the config

- **WHEN** `archivey.extract(src, dest, config=ArchiveyConfig(extraction_limits=ExtractionLimits(max_ratio=100)))` runs
- **THEN** the 100:1 per-member ratio limit is enforced (see `safe-extraction`)

---

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

The system SHALL support any number of member streams opened from a **single reader** being
held open and read in **interleaved** order without corrupting one another, when they are
served from one underlying source with a single file position. A backend that serves members
by **byte-range access into a shared source** MUST route member reads through a shared-source
view that keeps a **per-view position** and performs each seek+read as an atomic pair under
the source lock, so that reading one open stream never disturbs another open stream's position.

**Scope / carve-out.** This requirement applies to backends that serve members via independent
byte-range views over the source (e.g. the native 7z/RAR readers, single-file). A backend
that serves members through a **single shared decoder or parser object** — notably the
stdlib-`tarfile`-backed random-access TAR reader — is **exempt**: it MAY require that only one
member stream be open at a time (or serialize opens), and MUST NOT be expected to support
interleaved concurrent opens. A **solid** format (7z folder, RAR block) satisfies the
requirement by giving each `open()` its own decompressor over its own shared-source view,
re-decoding from the block start as the *random open on a solid member* scenario already
permits. Backends whose member addressing is owned by an external library that already
coordinates the shared handle (ISO via `pycdlib`, ZIP via stdlib `zipfile` — path and
stream sources alike, through its `_SharedFile`) are
outside this SharedSource retrofit; they are not listed as non-compliant under this
requirement.

**Single-reader guarantee only.** The reader object itself remains **not thread-safe**: it MUST
NOT be driven (concurrent `open()`, iteration, or `close()`) from multiple threads. That misuse
is **unsupported and undefined** — the reader holds no lock and does not detect it. The
guarantee here is confined to interleaved use of already-opened member streams from one thread.

**Failing loudly on detectable misuse.** Where the shared-source view *can* detect misuse — a
read after the source is closed — the reader surface SHALL raise a typed error (translated
from the primitive's stdlib-shaped error at the reader boundary; the `streamtools` primitive
itself raises `ValueError`/`OSError` and defines no archivey exception). A view whose
requested bounds extend past the source is **clamped** to the available bytes (like a real
stream), so a truncated archive still yields a short readable view rather than failing at
construction. This "fail loudly" clause covers only detectable primitive misuse (closed
handle), not the undefined multi-thread-reader case above.

#### Scenario: interleaved reads of two open members stay correct

- **WHEN** two members of a byte-range random-access archive are opened at the same time and
  read in an interleaved sequence of partial reads
- **THEN** each stream returns exactly its own member's bytes in order, regardless of the
  interleaving, and neither is affected by the other's position

#### Scenario: reading after the reader is closed fails loudly

- **WHEN** a member stream is read after its reader (and the underlying source) has been closed
- **THEN** a typed error is raised at the reader surface rather than returning arbitrary or
  empty bytes

#### Scenario: single-decoder backend need not support interleaved opens

- **WHEN** the random-access TAR reader (one shared `tarfile` object) is asked to hold two
  member streams open and interleave them
- **THEN** this is outside the concurrent-open guarantee; the backend MAY serve one member
  stream at a time without violating this requirement

### Requirement: Random-access member-open is reentrant and reader-state-free

The system SHALL require that, for a **random-access backend that advertises independent member
open** (`streaming=False`, serving members by byte-range access — e.g. the native 7z/RAR
readers, ZIP, single-file), the member-open implementation (`_open_member`) is a function of the
member and the archive's shared source only: it MUST NOT mutate shared reader state, and it MUST
NOT keep per-open scratch on the reader that a second concurrent open would overwrite. Any
byte-range access it performs MUST go through a shared-source view (per the *Multiple
concurrently-open member streams* requirement) rather than by seeking a single shared handle
directly. Backends MAY hold immutable, already-materialized state (the member list and name
index) read-only.

**Scope.** This invariant does **not** apply to forward-only/streaming reads (`streaming=True`),
which are inherently single-pass, nor to backends exempted from concurrent-open (a single shared
decoder/parser object, such as the random-access TAR reader). Backends whose member addressing
is owned by an external library that already coordinates the shared handle (ISO via `pycdlib`,
ZIP path-source via stdlib `zipfile`) are outside the SharedSource retrofit and are not required
to route opens through an archivey shared-source view; they remain subject to the no-per-open-
scratch rule on archivey-owned reader state. It is a forward-compatibility contract that keeps
the reader ABC ready for a future parallel-extraction consumer without an interface retrofit; it
does not by itself make the reader object thread-safe, and it imposes no ordering or performance
guarantee.

**Materialize-before-fan-out.** A future concurrent consumer MUST materialize the member list
(a completed random-access pass) before opening members concurrently; the one-time member-cache
build is not itself concurrency-safe. This precondition is documented on the ABC now so Phase 6
backends and any future consumer honor it.

#### Scenario: opening one member does not disturb another open

- **WHEN** a random-access backend that advertises independent member open serves two members
  opened concurrently from the same reader
- **THEN** neither open call has mutated reader state the other depends on, and each returned
  stream reads its own member's bytes correctly under interleaving

#### Scenario: member-open derives access from the shared source

- **WHEN** such a backend implements `_open_member`
- **THEN** it obtains the member's byte range through a shared-source view rather than by
  seeking a single shared handle in place, so concurrent opens cannot corrupt each other's
  position

#### Scenario: streaming and single-decoder backends are out of scope; ISO is library-owned

- **WHEN** the reader is a forward-only streaming pass, or a backend served by a single shared
  decoder object (random-access TAR)
- **THEN** this invariant does not apply and the backend is not required to support concurrent
  member opens
- **WHEN** the backend is ISO (`pycdlib` owns member addressing)
- **THEN** it is not required to route opens through an archivey shared-source view and is not
  listed as non-compliant; archivey-owned reader state still MUST NOT hold per-open scratch

