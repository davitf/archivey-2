# Archive Writing

## Purpose

Provides a uniform interface for creating new archives across all supported writable formats. The `ArchiveWriter` class allows adding entries from the filesystem, raw bytes, binary streams, or existing `Member` objects, and supports streaming conversion between archive formats without intermediate buffering of the whole archive.

## Requirements

### Requirement: Creating an archive for writing

The system SHALL expose a top-level `archivey.create()` function that accepts a destination path or binary stream and returns an `ArchiveWriter`.

```python
archivey.create(
    dest: str | Path | BinaryIO,
    format: ArchiveFormat,
    *,
    compression: CompressionSpec | None = None,
    password: str | bytes | None = None,
    encoding: str = "utf-8",
) -> ArchiveWriter
```

The `format` parameter is required. `compression` sets a default `CompressionSpec` applied to added entries unless overridden per-entry. `password` enables encryption where the target format supports it. `encoding` is used for legacy non-unicode path fields.

The `ArchiveWriter` implements the context-manager protocol; resources SHALL be finalized and flushed on exit.

```python
def __enter__(self) -> ArchiveWriter: ...
def __exit__(self, *_) -> None: ...
def close(self) -> None: ...
```

#### Scenario: create and close via context manager

- **WHEN** `with archivey.create("output.zip", ArchiveFormat.ZIP) as writer:` exits normally
- **THEN** the archive is finalized (central directory written, file handle closed)

#### Scenario: create with default compression

- **WHEN** `archivey.create("out.zip", ArchiveFormat.ZIP, compression=CompressionSpec.DEFLATE)` is called
- **THEN** entries added without a per-entry `compression` argument use DEFLATE at level 6

---

### Requirement: Adding entries from the filesystem

The system SHALL provide an `add()` method for adding files or directories from the local filesystem.

```python
def add(
    self,
    source: str | Path,
    *,
    name: str | None = None,       # override archive path
    recursive: bool = True,
    compression: CompressionSpec | None = None,
) -> None: ...
```

When `source` is a directory and `recursive=True`, all contained files and subdirectories SHALL be added. The `name` parameter overrides the archive-internal path for the entry. The per-entry `compression` parameter overrides the writer-level default.

#### Scenario: add a single file

- **WHEN** `writer.add("src/main.py")` is called
- **THEN** the file is added to the archive with its filesystem-relative path as the archive name

#### Scenario: add a directory recursively

- **WHEN** `writer.add("src/", name="source/", recursive=True)` is called
- **THEN** all files under `src/` are added, preserving relative path structure under `source/` in the archive

---

### Requirement: Adding entries from bytes or a binary stream

The system SHALL provide `add_bytes()` for in-memory data and `add_stream()` for streaming binary sources.

```python
def add_bytes(
    self,
    data: bytes | bytearray,
    name: str,
    *,
    modified: datetime | None = None,
    mode: int | None = None,
    compression: CompressionSpec | None = None,
) -> None: ...

def add_stream(
    self,
    stream: BinaryIO,
    name: str,
    *,
    size: int | None = None,       # required by some formats
    modified: datetime | None = None,
    mode: int | None = None,
    compression: CompressionSpec | None = None,
) -> None: ...
```

The `size` parameter in `add_stream()` is required by some formats that must write the size before the data; callers SHOULD provide it when known.

#### Scenario: add bytes with metadata

- **WHEN** `writer.add_bytes(b"Hello", name="greeting.txt", modified=dt)` is called
- **THEN** a member named `"greeting.txt"` is written with the given content and modification timestamp

#### Scenario: add from a BinaryIO stream

- **WHEN** `writer.add_stream(f, name="data/large.bin", size=file_size)` is called
- **THEN** the stream is read and written to the archive entry without loading it entirely into memory

---

### Requirement: Adding entries from Member objects

The system SHALL provide `add_member()` for writing an entry whose identity and metadata are described by an existing `Member` dataclass paired with a data stream.

```python
def add_member(self, member: Member, data: BinaryIO) -> None: ...
```

The writer SHALL use the metadata fields from `member` (name, mode, timestamps, etc.) and read data from `data`.

#### Scenario: transfer metadata from an existing Member

- **WHEN** `writer.add_member(member, stream)` is called
- **THEN** the archive entry is written with name, mode, and timestamps taken from `member` and content from `stream`

---

### Requirement: Streaming conversion via add_members

The system SHALL provide `add_members()` as a streaming conversion primitive that reads from an `ArchiveReader` and writes to this writer without buffering the whole archive in memory.

```python
def add_members(
    self,
    reader: ArchiveReader,
    *,
    members: Iterable[Member] | None = None,
) -> None: ...
```

`add_members()` MUST:
1. Iterate `reader` sequentially (via `stream_members()` internally, respecting solid-archive bounded-memory semantics).
2. For each member, open its data stream from the reader and pipe it into the writer using a configurable chunk size (default: 1 MiB).
3. Translate `Member` metadata (name, mode, timestamps) directly without re-encoding.
4. Skip members with types unsupported by the target format, emitting a `logging.WARNING` for each skipped member.
5. MUST NOT buffer the full archive in memory; the writer MAY buffer internally per its format requirements (e.g. ZIP local headers need the CRC before writing), but only on a per-member basis.

When `members` is provided, only those members SHALL be transferred; when `None`, all members are transferred.

#### Scenario: full archive conversion

- **WHEN** `writer.add_members(reader)` is called
- **THEN** all members from `reader` are streamed to `writer` in a single sequential pass without loading the whole archive into memory

#### Scenario: unsupported member type is skipped

- **WHEN** a member's type is not supported by the target format
- **THEN** the member is skipped and a `logging.WARNING` is emitted; no exception is raised

#### Scenario: chunk size limits memory

- **WHEN** data is piped from reader to writer
- **THEN** at most one chunk (default 1 MiB) of a member's data is held in memory at a time

---

### Requirement: CompressionSpec model and convenience constants

The system SHALL define a `CompressionSpec` dataclass that describes the compression algorithm and optional level for entries written to an archive.

```python
@dataclass
class CompressionSpec:
    algo: CompressionAlgo = CompressionAlgo.DEFLATE
    level: int | None = None   # None = library default

# Convenience constants:
CompressionSpec.STORED      = CompressionSpec(algo=CompressionAlgo.STORED)
CompressionSpec.DEFLATE     = CompressionSpec(algo=CompressionAlgo.DEFLATE, level=6)
CompressionSpec.DEFLATE_MAX = CompressionSpec(algo=CompressionAlgo.DEFLATE, level=9)
CompressionSpec.LZMA        = CompressionSpec(algo=CompressionAlgo.LZMA2, level=6)
```

`level=None` means the backend's library default is used. Convenience constants SHALL be available as class attributes on `CompressionSpec`.

#### Scenario: using a convenience constant

- **WHEN** `compression=CompressionSpec.DEFLATE` is passed to `archivey.create()` or any `add_*` method
- **THEN** entries are compressed with DEFLATE at level 6

#### Scenario: stored (no compression)

- **WHEN** `compression=CompressionSpec.STORED` is passed
- **THEN** entries are written without compression
