# Archive Writing

## Purpose

Provides a uniform interface for creating new archives across all supported writable formats. The `ArchiveWriter` class allows adding entries from the filesystem, raw bytes, binary streams, or existing `ArchiveMember` objects, and supports streaming conversion between archive formats without intermediate buffering of the whole archive.

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

The system SHALL provide an `add_file()` method for adding files or directories from the local filesystem. (It is named `add_file()` — not `add()` — to make clear it refers to an existing filesystem path, distinct from `add_bytes()`/`add_stream()`/`add_member()`.)

```python
def add_file(
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

- **WHEN** `writer.add_file("src/main.py")` is called
- **THEN** the file is added to the archive with its filesystem-relative path as the archive name

#### Scenario: add a directory recursively

- **WHEN** `writer.add_file("src/", name="source/", recursive=True)` is called
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

### Requirement: Adding entries from ArchiveMember objects

The system SHALL provide `add_member()` for writing an entry whose identity and metadata are described by an existing `ArchiveMember` dataclass paired with a data stream.

```python
def add_member(self, member: ArchiveMember, data: BinaryIO) -> None: ...
```

The writer SHALL use the metadata fields from `member` (name, mode, timestamps, etc.) and read data from `data`.

#### Scenario: transfer metadata from an existing ArchiveMember

- **WHEN** `writer.add_member(member, stream)` is called
- **THEN** the archive entry is written with name, mode, and timestamps taken from `member` and content from `stream`

---

### Requirement: Streaming conversion via add_members

The system SHALL provide `add_members()` as a streaming conversion primitive that writes members to this writer without buffering the whole archive in memory. It SHALL accept **either** an `ArchiveReader` (whole-archive conversion) **or** an iterable of `(member, stream)` pairs — the same shape `ArchiveReader.stream_members()` yields — so a caller can select/filter on the read side and pipe the result straight through:

```python
def add_members(
    self,
    source: ArchiveReader | Iterable[tuple[ArchiveMember, BinaryIO | None]],
) -> None: ...

# whole archive:
writer.add_members(reader)
# selected/filtered/renamed, in one streaming pass, no reopen:
writer.add_members(reader.stream_members(lambda m: m.name.endswith(".py"),
                                         filter=my_sanitizer))
```

Accepting the `(member, stream)` iterable (rather than a separate `add_members_from_iter`)
keeps selection and sanitization on the reader side via `stream_members(...)`, and avoids
the anti-pattern of passing a list of members back into the writer (which would force a
reopen). When given an `ArchiveReader`, `add_members()` drives `reader.stream_members()`
internally.

`add_members()` MUST:
1. Consume the source sequentially (a reader via `stream_members()`), respecting solid-archive bounded-memory semantics.
2. For each member, pipe its data stream into the writer using a configurable chunk size (default: 1 MiB).
3. Translate `ArchiveMember` metadata (name, mode, timestamps) directly without re-encoding.
4. Skip members with types unsupported by the target format, emitting a `logging.WARNING` for each skipped member.
5. MUST NOT buffer the full archive in memory; the writer MAY buffer internally per its format requirements (e.g. ZIP local headers need the CRC before writing), but only on a per-member basis.

#### Scenario: full archive conversion

- **WHEN** `writer.add_members(reader)` is called
- **THEN** all members from `reader` are streamed to `writer` in a single sequential pass without loading the whole archive into memory

#### Scenario: selected/filtered conversion in one pass

- **WHEN** `writer.add_members(reader.stream_members(predicate, filter=sanitizer))` is called
- **THEN** only the selected, sanitized members are written, in a single streaming pass, without reopening the source archive

#### Scenario: unsupported member type is skipped

- **WHEN** a member's type is not supported by the target format
- **THEN** the member is skipped and a `logging.WARNING` is emitted; no exception is raised

#### Scenario: chunk size limits memory

- **WHEN** data is piped from reader to writer
- **THEN** at most one chunk (default 1 MiB) of a member's data is held in memory at a time

---

### Requirement: CompressionSpec model and convenience constants

The system SHALL define a `CompressionSpec` dataclass describing the compression
algorithm and level for entries written to an archive. The `algo` field is **nullable**
(`None` = let the backend choose the appropriate algorithm for the format and level),
and `level` accepts either a numeric value **or** a format-agnostic `CompressionLevel`
enum so callers can ask for a relative effort without knowing a format's numeric scale.

```python
class CompressionLevel(Enum):
    STORE   = "store"     # no compression
    FAST    = "fast"      # fastest meaningful compression
    DEFAULT = "default"   # the backend's balanced default
    MAX     = "max"       # maximum compression the algorithm offers

@dataclass
class CompressionSpec:
    algo: CompressionAlgo | None = None                 # None = backend auto-selects
    level: int | CompressionLevel = CompressionLevel.DEFAULT

# Convenience constants:
CompressionSpec.STORED      = CompressionSpec(algo=CompressionAlgo.STORED)
CompressionSpec.DEFLATE     = CompressionSpec(algo=CompressionAlgo.DEFLATE, level=6)
CompressionSpec.DEFLATE_MAX = CompressionSpec(algo=CompressionAlgo.DEFLATE, level=CompressionLevel.MAX)
CompressionSpec.LZMA        = CompressionSpec(algo=CompressionAlgo.LZMA2, level=CompressionLevel.DEFAULT)
```

**Resolution table** (how `(algo, level)` is resolved by the backend). `compression=None`
at `create()`/`add_*` is equivalent to `CompressionSpec(algo=None, level=DEFAULT)`.

| `algo` | `level` | Behavior |
|--------|---------|----------|
| `None` | `STORE`/`FAST`/`DEFAULT`/`MAX` | Backend **chooses the algorithm** appropriate for the format and the requested effort (a higher level MAY select a different algorithm), then applies that effort. `STORE` selects `STORED` (no compression). |
| `None` | numeric `int` | Backend uses the format's **default algorithm** at the given numeric level (or, where a format derives the algorithm from the level, the algorithm implied by it). |
| set | `STORE` | Resolves to `STORED` (no compression); the explicit `algo` is overridden and a `logging.WARNING` is emitted noting the contradiction. |
| set | `FAST`/`DEFAULT`/`MAX` | Uses that algorithm, mapping the symbolic level to that algorithm's nearest concrete level. |
| set | numeric `int` | Uses that algorithm at that numeric level. If the value is outside the algorithm's valid range, the backend raises `ValueError` (it does **not** silently clamp). |

Convenience constants SHALL be available as class attributes on `CompressionSpec`.

#### Scenario: auto algorithm at a symbolic level

- **WHEN** `compression=CompressionSpec(algo=None, level=CompressionLevel.MAX)` is passed to `archivey.create("out.zip", ArchiveFormat.ZIP, ...)`
- **THEN** the backend selects an appropriate algorithm for ZIP and applies maximum effort

#### Scenario: default compression when omitted

- **WHEN** `compression=None` (or omitted) is passed
- **THEN** it is treated as `CompressionSpec(algo=None, level=CompressionLevel.DEFAULT)` — the backend's auto algorithm at its balanced default

#### Scenario: using a convenience constant

- **WHEN** `compression=CompressionSpec.DEFLATE` is passed to `archivey.create()` or any `add_*` method
- **THEN** entries are compressed with DEFLATE at level 6

#### Scenario: STORE level overrides an explicit algorithm

- **WHEN** `compression=CompressionSpec(algo=CompressionAlgo.LZMA2, level=CompressionLevel.STORE)` is passed
- **THEN** the entry is written `STORED` (uncompressed) and a `logging.WARNING` notes the contradictory combination

#### Scenario: out-of-range numeric level is rejected

- **WHEN** a numeric `level` outside the chosen algorithm's valid range is passed
- **THEN** `ValueError` is raised rather than silently clamping
