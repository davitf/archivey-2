# Archive Writing

## Purpose

Uniform interface for creating new archives across writable formats. `ArchiveWriter`
adds entries from the filesystem, bytes, binary streams, or existing
`ArchiveMember` objects, and supports streaming conversion from readers without
buffering the whole archive.

## Related specs

| Spec | Relationship |
| --- | --- |
| `archive-reading` | `ArchiveReader.stream_members()` source shape for conversion |
| `archive-data-model` | `ArchiveMember`, `CompressionAlgorithm`, metadata fields |
| `backend-registry` | Missing writer backend / codec error and install-hint behavior |
| `packaging-and-extras` | `[7z-write]`, `[zstd]`, and other optional write dependencies |
| `logging` | Warnings for skipped members and contradictory compression settings |
| `safe-extraction` | Shared member-filter transform semantics |

## Requirements

### Requirement: Creating an archive for writing

The system SHALL expose `archivey.create()` returning an `ArchiveWriter`:

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

`format` is required. `compression` SHALL set a writer default used by added
entries unless overridden per entry. `password` SHALL enable encryption where the
target format supports it. `encoding` SHALL be used for legacy non-Unicode path
fields.

`ArchiveWriter` SHALL implement context-manager lifecycle and finalize/flush
resources on exit:

```python
def __enter__(self) -> ArchiveWriter: ...
def __exit__(self, *_) -> None: ...
def close(self) -> None: ...
```

#### Scenario: writer creation matrix

| Case | Expected |
| --- | --- |
| `with archivey.create("output.zip", ArchiveFormat.ZIP) as writer:` exits normally | Archive finalized, central directory written, file handle closed |
| `compression=CompressionSpec.DEFLATE` at create | Entries without per-entry compression use DEFLATE at level 6 |
| `format` omitted | Call is invalid because the target format is required |

### Requirement: Adding entries from the filesystem

The system SHALL provide `add_file()` for adding existing filesystem files or
directories. It MUST be named `add_file()` to distinguish filesystem paths from
`add_bytes()`, `add_stream()`, and `add_member()`.

```python
def add_file(
    self,
    source: str | Path,
    *,
    name: str | None = None,
    recursive: bool = True,
    compression: CompressionSpec | None = None,
) -> None: ...
```

When `source` is a directory and `recursive=True`, the writer SHALL add all
contained files and subdirectories. `name` SHALL override the archive-internal path.
Per-entry `compression` SHALL override the writer default.

#### Scenario: filesystem-add matrix

| Case | Expected |
| --- | --- |
| `writer.add_file("src/main.py")` | File added with its filesystem-relative archive name |
| `writer.add_file("src/", name="source/", recursive=True)` | Directory tree added under `source/`, preserving relative paths |
| Per-entry compression supplied | Entry uses that compression instead of writer default |

### Requirement: Adding entries from bytes, streams, or members

The system SHALL provide `add_bytes()` for in-memory data, `add_stream()` for
streaming binary sources, and `add_member()` for copying metadata from an existing
`ArchiveMember` while reading bytes from a stream.

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
    size: int | None = None,
    modified: datetime | None = None,
    mode: int | None = None,
    compression: CompressionSpec | None = None,
) -> None: ...

def add_member(self, member: ArchiveMember, data: BinaryIO) -> None: ...
```

`add_stream()` SHALL stream from `BinaryIO` without loading the whole source into
memory. `size` SHOULD be provided when known and MAY be required by formats that
must write size before data. `add_member()` SHALL use the member's name, mode,
timestamps, and other representable metadata, and read content from `data`.

#### Scenario: direct-entry matrix

| Case | Expected |
| --- | --- |
| `add_bytes(b"Hello", name="greeting.txt", modified=dt)` | Member written with given bytes and timestamp |
| `add_stream(f, name="data/large.bin", size=file_size)` | Stream copied into entry without whole-source materialization |
| `add_member(member, stream)` | Entry uses member metadata and stream content |

### Requirement: Streaming conversion via add_members

The system SHALL provide `add_members()` as a streaming conversion primitive. It
MUST accept either an `ArchiveReader` for whole-archive conversion or an iterable of
`(ArchiveMember, BinaryIO | None)` pairs matching `ArchiveReader.stream_members()`.
It MUST NOT force callers to pass a materialized list of members back into the
writer.

```python
def add_members(
    self,
    source: ArchiveReader | Iterable[tuple[ArchiveMember, BinaryIO | None]],
    *,
    filter: MemberFilter | None = None,
) -> None: ...
```

Reader-side selection SHALL happen with `stream_members(members=...)`. Writer-side
transformation SHALL happen with `filter`, a
`Callable[[ArchiveMember], ArchiveMember | None]` shared with extraction. The filter
MUST apply to a transient `.replace()` copy used for the written entry's identity,
while the original mutable member and stream continue through the backend so
late-bound updates stay visible. A filter returning `None` skips the member.

`add_members()` MUST consume sources sequentially, drive an `ArchiveReader` through
`stream_members()` internally, respect solid-archive bounded-memory semantics, pipe
member data in chunks with default chunk size 1 MiB, translate member metadata
directly, skip unsupported member types with a `logging.WARNING`, and never buffer
the full archive in memory. Format-internal buffering MAY occur only per member when
required, such as ZIP local headers needing CRC before writing.

#### Scenario: streaming-conversion matrix

| Case | Expected |
| --- | --- |
| `writer.add_members(reader)` | All members stream in one sequential pass without whole-archive buffering |
| `writer.add_members(reader.stream_members(predicate), filter=sanitizer)` | Selected members are written after writer-side copy transform, in one pass, without reopening |
| `filter` returns `None` | Member skipped |
| Target format cannot represent a member type | Member skipped and `logging.WARNING` emitted; no exception |
| Reader data piped to writer | At most one member chunk, default 1 MiB, is held by conversion code |

### Requirement: CompressionSpec model and convenience constants

The system SHALL define `CompressionSpec` for writer compression choices. Its
`algo` field SHALL reuse `CompressionAlgorithm` from `archive-data-model` and be
nullable (`None` means backend auto-selects). Its `level` field SHALL accept either
a numeric value or a format-agnostic `CompressionLevel` enum.

```python
class CompressionLevel(Enum):
    STORE = "store"
    FAST = "fast"
    DEFAULT = "default"
    MAX = "max"

@dataclass
class CompressionSpec:
    algo: CompressionAlgorithm | None = None
    level: int | CompressionLevel = CompressionLevel.DEFAULT

CompressionSpec.STORED = CompressionSpec(algo=CompressionAlgorithm.STORED)
CompressionSpec.DEFLATE = CompressionSpec(algo=CompressionAlgorithm.DEFLATE, level=6)
CompressionSpec.DEFLATE_MAX = CompressionSpec(
    algo=CompressionAlgorithm.DEFLATE,
    level=CompressionLevel.MAX,
)
CompressionSpec.LZMA = CompressionSpec(
    algo=CompressionAlgorithm.LZMA2,
    level=CompressionLevel.DEFAULT,
)
```

Convenience constants SHALL be class attributes. `compression=None` at `create()` or
`add_*` SHALL be equivalent to `CompressionSpec(algo=None, level=DEFAULT)`.

| `algo` | `level` | Behavior |
| --- | --- | --- |
| `None` | `STORE` / `FAST` / `DEFAULT` / `MAX` | Backend chooses a format-appropriate available algorithm for the requested effort; `STORE` selects `STORED` |
| `None` | numeric `int` | Backend uses the format default algorithm at that numeric level, or the algorithm implied by the level |
| set | `STORE` | Resolves to `STORED`; emits `logging.WARNING` for the contradiction |
| set | `FAST` / `DEFAULT` / `MAX` | Uses that algorithm, mapping the symbolic level to the nearest concrete level |
| set | numeric `int` | Uses that algorithm at that level; out-of-range values raise `ValueError` and are not clamped |

When the caller names an explicit `algo` whose backend is unavailable or whose
target format cannot represent it, `create()` or the first `add_*` that would use
it SHALL fail fast with `PackageNotInstalledError` or `UnsupportedFeatureError`.
The system MUST NOT silently substitute a different algorithm or degrade to the
format default. With `algo=None`, the backend SHALL choose an available algorithm.

#### Scenario: compression-resolution matrix

| Case | Expected |
| --- | --- |
| Explicit `ZSTD` without `[zstd]` | `PackageNotInstalledError`; no archive written; no fallback codec |
| `algo=None`, `level=MAX` for ZIP | Backend selects an appropriate available ZIP algorithm at maximum effort |
| `compression=None` or omitted | Treated as backend auto algorithm at default effort |
| `CompressionSpec.DEFLATE` | Entries use DEFLATE level 6 |
| Explicit `LZMA2` with `level=STORE` | Entry written uncompressed and warning emitted |
| Numeric level outside algorithm range | `ValueError`; no silent clamp |
