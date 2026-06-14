# Archive Reading

## Purpose

Provides a uniform interface for opening and reading archives across all supported formats. The `ArchiveReader` class presents ZIP, TAR, RAR, 7z, ISO, plain directories, and single-file compressed streams as interchangeable objects with consistent metadata, iteration, and data-access semantics.

## Requirements

### Requirement: Opening an archive for reading

The system SHALL expose a top-level `archivey.open()` function that accepts a file path, `Path`, or binary stream and returns an `ArchiveReader`.

```python
archivey.open(
    source: str | Path | BinaryIO,
    *,
    format: ArchiveFormat | None = None,  # override detection
    intent: Intent = Intent.AUTO,
    password: str | bytes | None = None,
    encoding: str = "utf-8",             # fallback for legacy non-unicode paths
) -> ArchiveReader
```

The `format` parameter MAY be omitted; when omitted the library performs automatic format detection. The `encoding` parameter is used as a fallback for legacy non-unicode path fields in the archive.

#### Scenario: open with auto-detected format

- **WHEN** `archivey.open("archive.tar.gz")` is called with no `format` override
- **THEN** the library detects the format from magic bytes and returns an `ArchiveReader` wrapping the appropriate backend

#### Scenario: open with explicit format override

- **WHEN** `archivey.open(source, format=ArchiveFormat.ZIP)` is called
- **THEN** the library uses the specified format backend without running detection

#### Scenario: open with password

- **WHEN** `archivey.open(source, password="secret")` is called
- **THEN** the returned `ArchiveReader` uses the provided password for encrypted members

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

`info` returns an `ArchiveInfo` dataclass (format, version, solid flag, member count, comment, encryption, multivolume status, and cost). `cost` returns a `CostReceipt` describing the listing cost, access cost, stream capability, and solid block count. `format` returns the `ArchiveFormat` enum value for the open archive.

#### Scenario: access info after open

- **WHEN** an archive is successfully opened
- **THEN** `ar.info`, `ar.cost`, and `ar.format` are immediately available without triggering additional I/O

---

### Requirement: Sequential in-order iteration

The system SHALL support iterating all members in archive order via `__iter__`, and MAY materialize the full member list via `members()` or `__len__`.

```python
def __iter__(self) -> Iterator[Member]: ...     # sequential, in-order
def members(self) -> list[Member]: ...          # materializes all (may trigger scan)
def __len__(self) -> int: ...                   # may trigger scan
def __contains__(self, name: str) -> bool: ...
```

`__iter__` MUST yield `Member` objects one at a time without loading all members into memory. `members()` and `__len__` MAY trigger a full scan for streaming formats that have no central directory. After the member list has been materialized once, subsequent `__iter__` calls MUST return from the cache rather than re-reading the archive.

When opened with `Intent.SEQUENTIAL`, calling `members()` or `__len__` SHALL raise `UnsupportedOperationError` because those methods require materializing all members.

#### Scenario: forward iteration

- **WHEN** `for member in ar` is executed
- **THEN** the reader yields `Member` objects in archive order without buffering all of them in memory

#### Scenario: materialization on sequential intent

- **WHEN** `ar.members()` or `len(ar)` is called on a reader opened with `Intent.SEQUENTIAL`
- **THEN** `UnsupportedOperationError` is raised

---

### Requirement: Membership and random access by name

The system SHALL support dictionary-style lookup of members by normalized name, subject to an intent constraint.

```python
def __getitem__(self, name: str) -> Member: ...    # KeyError if absent
def get(self, name: str, default=None) -> Member | None: ...
```

Calling `__getitem__`, `get`, or random `extract` on a reader opened with `Intent.SEQUENTIAL` SHALL raise `UnsupportedOperationError` unless the backend can satisfy it cheaply (e.g. the archive has an in-memory index already loaded).

#### Scenario: successful key lookup

- **WHEN** `ar["path/to/file.txt"]` is called and the member exists
- **THEN** the corresponding `Member` object is returned

#### Scenario: missing key lookup

- **WHEN** `ar["nonexistent.txt"]` is called and the member does not exist
- **THEN** `KeyError` is raised

#### Scenario: random access on sequential-intent reader

- **WHEN** `ar["file.txt"]` is called on a reader opened with `Intent.SEQUENTIAL` and the backend cannot satisfy it cheaply
- **THEN** `UnsupportedOperationError` is raised

---

### Requirement: Reading member data

The system SHALL provide two data-access methods: `read()` which returns the full member content as `bytes`, and `open()` which returns a streaming `BinaryIO` that the caller is responsible for closing.

```python
def read(self, member: str | Member) -> bytes: ...
def open(self, member: str | Member) -> BinaryIO: ...   # streaming; caller must close
```

Both methods accept either a member name string or a `Member` object.

#### Scenario: reading member as bytes

- **WHEN** `ar.read("readme.txt")` is called
- **THEN** the full uncompressed content is returned as `bytes`

#### Scenario: opening a member as a stream

- **WHEN** `ar.open("data.bin")` is called
- **THEN** a `BinaryIO` stream is returned; the caller reads from it and closes it when done

---

### Requirement: Bounded-memory sequential streaming via stream_members

The system SHALL provide `stream_members()` which yields `(member, stream)` pairs in archive order with bounded memory: each solid block is decompressed once and its memory released before the next block starts. The yielded stream is only valid until the iterator advances; callers MUST NOT hold it across yields.

```python
def stream_members(self) -> Iterator[tuple[Member, BinaryIO]]: ...
```

**Two sequential access patterns — different memory profiles:**

| Pattern | Memory profile | When to use |
|---------|---------------|-------------|
| `for m in ar: ar.open(m)` | Monotonically growing — each solid block is extracted into a cache that persists until `close()`. Peak = sum of all solid blocks accessed. | Random or mixed access; when you may revisit members. |
| `for m, f in ar.stream_members()` | Bounded — each solid block is extracted, yielded, then released before the next starts. Peak = largest single solid block. | Sequential one-pass processing: hashing, conversion, scanning. |

For formats without solid compression (ZIP, TAR, plain .gz), both patterns are equally efficient — there is no caching in either path.

#### Scenario: streaming a solid archive

- **WHEN** `ar.stream_members()` is called on a solid archive (e.g. 7z)
- **THEN** each solid block is decompressed exactly once, yielded, and its memory released before the next block is started
- **AND** peak memory equals the size of the largest single solid block

#### Scenario: stream is invalid after advance

- **WHEN** the iterator advances to the next `(member, stream)` pair
- **THEN** the previously yielded stream MUST NOT be used; it is no longer guaranteed to be valid

---

### Requirement: Transparent link following

The system SHALL transparently follow symlinks and hardlinks in `open()` and `read()`. If `member.type` is `SYMLINK` or `HARDLINK`, the call is redirected to `open(reader[member.link_target])`. This behavior is format-independent and is implemented once in the `ArchiveReader` ABC.

If the link target is not present in the archive, `ReadError` SHALL be raised. If the link target is itself a link, it SHALL be followed recursively up to a maximum depth of 8; beyond this depth `ReadError` SHALL be raised to prevent cycles.

```python
# ABC implementation (ARCHITECTURE.md §2.3)
def open(self, member: str | Member, _depth: int = 0) -> BinaryIO:
    if isinstance(member, str):
        member = self[member]
    if member.type in (MemberType.SYMLINK, MemberType.HARDLINK) and member.link_target:
        if _depth > 8:
            raise ReadError("Symlink chain too deep (possible cycle)")
        target = self.get(member.link_target)
        if target is None:
            raise ReadError(f"Link target '{member.link_target}' not in archive")
        return self.open(target, _depth=_depth + 1)
    return self._open_member(member)
```

This does not rely on format-level link resolution; format-level resolution (e.g. rarfile following RAR5 hardlinks internally) happens at a lower level.

#### Scenario: reading via a symlink member

- **WHEN** `ar.read("data/latest")` is called and `"data/latest"` is a `SYMLINK` pointing to `"data/v1.0/report.txt"`
- **THEN** the content of `"data/v1.0/report.txt"` is returned transparently

#### Scenario: link target not in archive

- **WHEN** `ar.open(link_member)` is called and `link_member.link_target` is absent from the archive
- **THEN** `ReadError` is raised

#### Scenario: link cycle or depth exceeded

- **WHEN** following links recursively exceeds depth 8
- **THEN** `ReadError` is raised

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

- **WHEN** `with archivey.open("archive.zip") as ar:` exits (normally or via exception)
- **THEN** all backend resources (file handles, temp directories, caches) are released
