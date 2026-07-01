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
    password: str | bytes | None = None,
    encoding: str | None = None,         # None = auto-detect member-name encoding
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

The system SHALL support iterating all members in archive order via `__iter__`, and MAY materialize the full member list via `members()` or `__len__`.

```python
def __iter__(self) -> Iterator[ArchiveMember]: ...     # sequential, in-order
def members(self) -> list[ArchiveMember]: ...          # materializes all (may trigger scan)
def get_members_if_available(self) -> list[ArchiveMember] | None: ...  # no-scan peek
def __len__(self) -> int: ...                   # may trigger scan
def __contains__(self, name: str) -> bool: ...
```

`__iter__` MUST yield `ArchiveMember` objects one at a time without loading all members into memory. `members()` and `__len__` MAY trigger a full scan for streaming formats that have no central directory. After the member list has been materialized once, subsequent `__iter__` calls MUST return from the cache rather than re-reading the archive.

`get_members_if_available()` returns the member list only when it is available **without scanning** (already materialized, or the backend has a true upfront index), else `None`; it never scans, so it is callable under any intent. See `access-mode-and-cost` for its full contract.

When opened with `streaming=True`, the reader is forward-only: `members()`, `__len__`, `__contains__`, `__getitem__`, `get()`, `open()`, and `read()` all SHALL raise `UnsupportedOperationError` (uniformly, not depending on a loaded index). Only a single forward pass — `__iter__`/`stream_members` or one `extract_all` — plus `get_members_if_available()` is allowed. See the access mode × method table in `access-mode-and-cost`.

#### Scenario: forward iteration

- **WHEN** `for member in ar` is executed
- **THEN** the reader yields `ArchiveMember` objects in archive order without buffering all of them in memory

#### Scenario: materialization on a streaming reader

- **WHEN** `ar.members()` or `len(ar)` is called on a reader opened with `streaming=True`
- **THEN** `UnsupportedOperationError` is raised

---

### Requirement: Membership and random access by name

The system SHALL support dictionary-style lookup of members by normalized name, subject to an access-mode constraint.

```python
def __getitem__(self, name: str) -> ArchiveMember: ...    # KeyError if absent
def get(self, name: str, default=None) -> ArchiveMember | None: ...
```

Calling `__getitem__` or `get` on a reader opened with `streaming=True` SHALL raise `UnsupportedOperationError` — uniformly, regardless of whether the backend has an index loaded (a streaming reader is forward-only; this keeps its behaviour deterministic across formats). A caller that wants a no-scan peek at the member list on any reader uses `get_members_if_available()` instead.

#### Scenario: successful key lookup

- **WHEN** `ar["path/to/file.txt"]` is called and the member exists
- **THEN** the corresponding `ArchiveMember` object is returned

#### Scenario: missing key lookup

- **WHEN** `ar["nonexistent.txt"]` is called and the member does not exist
- **THEN** `KeyError` is raised

#### Scenario: random access on a streaming reader

- **WHEN** `ar["file.txt"]` is called on a reader opened with `streaming=True`
- **THEN** `UnsupportedOperationError` is raised (regardless of any loaded index)

---

### Requirement: Reading member data

The system SHALL provide two data-access methods: `read()` which returns the full member content as `bytes`, and `open()` which returns a streaming `BinaryIO` that the caller is responsible for closing.

```python
def read(self, member: str | ArchiveMember) -> bytes: ...
def open(self, member: str | ArchiveMember) -> BinaryIO: ...   # streaming; caller must close
```

Both methods accept either a member name string or an `ArchiveMember` object.

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
so unselected members cost nothing.

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

The system SHALL transparently follow symlinks and hardlinks in `open()` and `read()`. If `member.type` is `SYMLINK` or `HARDLINK`, the call is redirected to the target member. This behavior is format-independent and is implemented once in the `ArchiveReader` ABC. The resolved target, when known, is also exposed as `member.link_target_member`.

**Hardlinks** SHALL always resolve to an **earlier** member (this is the TAR model, in
which a hardlink entry refers back to a previously-seen file); the library relies on
this ordering so a hardlink can always be resolved during a single forward pass.

If the link target is not present in the archive, `LinkTargetNotFoundError` (a
`ReadError`/member error) SHALL be raised. Chains SHALL be followed recursively with
**cycle detection** — the set of members already visited on the current chain is
tracked, and if a member is revisited the library raises a `ReadError` reporting the
cycle. There is no fixed depth limit; an acyclic chain of any length resolves, and only
an actual cycle (or a missing target) fails.

```python
# ABC implementation (ARCHITECTURE.md §2.3)
def open(self, member: str | ArchiveMember, _seen: frozenset[int] = frozenset()) -> BinaryIO:
    if isinstance(member, str):
        member = self[member]
    if member.type in (MemberType.SYMLINK, MemberType.HARDLINK) and member.link_target:
        if member.member_id in _seen:
            raise ReadError(f"Link cycle detected at '{member.name}'")
        target = member.link_target_member or self.get(member.link_target)
        if target is None:
            raise LinkTargetNotFoundError(f"Link target '{member.link_target}' not in archive")
        return self.open(target, _seen=_seen | {member.member_id})
    return self._open_member(member)
```

This does not rely on format-level link resolution; format-level resolution (e.g. a RAR5 reader following hardlinks internally) happens at a lower level.

#### Scenario: reading via a symlink member

- **WHEN** `ar.read("data/latest")` is called and `"data/latest"` is a `SYMLINK` pointing to `"data/v1.0/report.txt"`
- **THEN** the content of `"data/v1.0/report.txt"` is returned transparently

#### Scenario: hardlink resolves to an earlier member

- **WHEN** a `HARDLINK` member is read and its target is an earlier member in archive order
- **THEN** the earlier member's content is returned, resolved in a single forward pass

#### Scenario: link target not in archive

- **WHEN** `ar.open(link_member)` is called and `link_member.link_target` is absent from the archive
- **THEN** `LinkTargetNotFoundError` is raised

#### Scenario: link cycle detected

- **WHEN** following a link chain revisits a member already seen on that chain
- **THEN** `ReadError` is raised reporting the cycle (no fixed depth limit is used; only genuine cycles fail)

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
