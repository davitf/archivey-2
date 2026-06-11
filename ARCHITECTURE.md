# Archivey — Architecture and Design Decisions

> This document explains the architecture of the library, the key design decisions, and the trade-offs considered. Read SPEC.md first for the external contract; this document explains the internals.

---

## 1. Module Layout

```
src/archivey/
├── __init__.py            # Public API re-exports: open(), create(), extract(), detect_format()
├── py.typed               # PEP 561 marker
│
├── _types.py              # All public types (Member, ArchiveInfo, CostReceipt, all enums)
├── _errors.py             # Exception hierarchy
├── _reader.py             # ArchiveReader ABC + default method implementations
├── _writer.py             # ArchiveWriter ABC
├── _detection.py          # Format detection engine + PeekableStream
├── _filters.py            # ExtractionPolicy filters and path sanitizer
├── _extraction.py         # Safe extraction coordinator (uses _filters.py)
├── _progress.py           # ExtractionProgress, ExtractionResult
│
└── backends/
    ├── __init__.py        # BackendRegistry singleton + register()
    ├── _base.py           # Backend ABC
    ├── _zip.py            # ZIP (zipfile stdlib)
    ├── _tar.py            # TAR all variants (tarfile stdlib)
    ├── _single.py         # GZ, BZ2, XZ single-file compressors
    ├── _dir.py            # Directory pseudo-backend
    ├── _7z.py             # 7-Zip (py7zr, optional)
    ├── _rar.py            # RAR (rarfile + unrar, optional)
    └── _iso.py            # ISO 9660 (pycdlib, optional)

tests/
├── corpus/                # Static test archives (committed binary files)
│   ├── adversarial/       # zip bombs, path traversal, corrupt archives
│   └── equivalence/       # same logical dir as zip/tar/7z/rar/iso
├── conftest.py
├── test_detection.py
├── test_types.py
├── test_zip.py
├── test_tar.py
├── test_single.py
├── test_7z.py
├── test_rar.py
├── test_iso.py
├── test_extraction.py     # filter/security tests
├── test_writing.py
├── test_conversion.py
└── test_equivalence.py    # equivalence matrix
```

---

## 2. Key Design Decisions

### 2.1 Frozen dataclasses for Member

`Member` is a `@dataclass(frozen=True)`. This is deliberate:

- **Thread safety:** immutable objects can be freely shared across threads without locks.
- **Hashability:** allows `set[Member]` and use as dict keys (useful in equivalence tests).
- **Accidental mutation prevention:** backends build a Member once; callers cannot corrupt it.

Trade-off: construction requires all fields up-front. For formats that stream metadata incrementally (e.g. TAR without pre-reading), this means accumulating fields before constructing the object. This is acceptable because the `Member` represents completed metadata, not an in-flight parse state.

For large archives, the backend yields `Member` objects one at a time via a generator — we never build a `list[Member]` unless the caller calls `.members()`. This keeps peak memory O(1) during sequential iteration.

### 2.2 Backend as a pure factory, not a stateful reader

The `Backend` class is a stateless factory. `open_read()` returns an `ArchiveReader` instance that holds all state. This separation allows:
- Multiple readers open simultaneously from the same backend class.
- Easy testing: mock `Backend.open_read()` to return a fake reader.
- Clean registration: backends register their class, not instances.

### 2.3 Single ArchiveReader ABC for all backends

Rather than having backend-specific reader classes be the public API, all backends return objects that implement the `ArchiveReader` ABC. The ABC provides default implementations for methods like `extract()` and `extract_all()` that delegate to the `_extraction.py` module — backends only need to implement iteration and raw data access.

```
ArchiveReader (ABC in _reader.py)
├── ZipReader    (backends/_zip.py)
├── TarReader    (backends/_tar.py)
├── SingleFileReader (backends/_single.py)
├── SevenZReader (backends/_7z.py)
├── RarReader    (backends/_rar.py)
├── IsoReader    (backends/_iso.py)
└── DirReader    (backends/_dir.py)
```

The only methods that backends **must** implement:
```python
@abstractmethod
def _iter_members(self) -> Iterator[Member]: ...        # sequential

@abstractmethod
def _open_member(self, member: Member) -> BinaryIO: ... # raw data stream

def _get_member_by_name(self, name: str) -> Member:     # optional override
    # default: linear scan of _iter_members() — backends with indexes override this
```

Everything else (`__iter__`, `__getitem__`, `read`, `open`, `extract`, `extract_all`, `members`) is implemented once in the ABC.

### 2.4 Lazy member materialization

`__iter__` calls `_iter_members()` directly — a generator that never loads all members.

`members()` and `__len__` force materialization:
```python
def members(self) -> list[Member]:
    if self._members_cache is None:
        self._members_cache = list(self._iter_members())
    return self._members_cache
```

After materialization, `__iter__` returns `iter(self._members_cache)` for efficiency (avoids re-reading on second iteration).

**Sequential intent guard:** if `intent == Intent.SEQUENTIAL`, materialization is forbidden. Calling `.members()` or `__len__` raises `UnsupportedOperationError` with a clear message.

### 2.5 PeekableStream for non-seekable sources

```
┌──────────────────────────────────────────────────────────────┐
│  PeekableStream                                              │
│  ┌─────────────────────┐    ┌────────────────────────────┐  │
│  │ buffer: bytearray   │    │ underlying: RawIO (socket) │  │
│  │ (first LIMIT bytes) │    │                            │  │
│  └─────────────────────┘    └────────────────────────────┘  │
│   └──► replayed on first read    └──► then transparently    │
│         by backend                     passed through        │
└──────────────────────────────────────────────────────────────┘
```

`PeekableStream` is only constructed when `source` is non-seekable. It wraps the stream, buffers the first `DETECTION_LIMIT` bytes (4 KiB by default, 32 KiB for ISO), exposes a `peek(n: int) -> bytes` method, and then presents itself as a regular `BinaryIO` to the backend. Reads drain from the buffer first, then fall through to the underlying stream.

This is a standard "read-ahead buffer" pattern — the key property is that the backend never knows or cares whether the source was originally seekable.

### 2.6 Extraction as a separate, composable module

`_extraction.py` implements the safe extraction coordinator as a pure function:

```python
def extract_member(
    member: Member,
    open_fn: Callable[[Member], BinaryIO],
    dest: Path,
    policy: ExtractionPolicy,
    overwrite: OverwritePolicy,
    bomb_tracker: BombTracker,
) -> ExtractionResult:
```

This function:
1. Applies `_filters.py` to the member (path check, type check, permission transform).
2. Handles the overwrite policy.
3. Creates directories as needed, atomically via `Path.mkdir(parents=True, exist_ok=True)`.
4. For files: opens the source via `open_fn`, copies in chunks, tracks bytes via `BombTracker`.
5. For symlinks: creates after all files (two-pass to handle ordering).
6. Sets metadata (mtime, permissions) on a best-effort basis after writing.

The coordinator is a pure function with no knowledge of archive formats. `ArchiveReader.extract_all()` in the ABC calls it in a loop.

### 2.7 Two-pass extraction for hardlinks and symlinks

Symlinks and hardlinks may reference files that appear later in the archive. The coordinator uses a deferred list:

```
Pass 1: Extract all FILE and DIRECTORY members
         Collect SYMLINK and HARDLINK members → deferred[]

Pass 2: Process deferred[]
         For HARDLINK: os.link(already_extracted_target, dest_path)
                       If cross-device: shutil.copy2 with warning
         For SYMLINK:  os.symlink(link_target, dest_path)
                       Verify resolution stays within dest root (post-creation check)
```

### 2.8 Filters as pure transform functions

```python
# _filters.py

def check_universal(member: Member) -> None:
    """Raises FilterRejectionError if member violates universal constraints."""

def transform_strict(member: Member) -> Member:
    """Returns a new Member with permissions adjusted for STRICT policy."""

def transform_standard(member: Member) -> Member:
    """Returns a new Member with permissions adjusted for STANDARD policy."""

POLICY_TRANSFORMS: dict[ExtractionPolicy, Callable[[Member], Member]] = {
    ExtractionPolicy.STRICT:   transform_strict,
    ExtractionPolicy.STANDARD: transform_standard,
    ExtractionPolicy.TRUSTED:  lambda m: m,  # identity
}
```

`check_universal` always runs first. Policy transforms always run second. The result is a new (immutable) `Member` with adjusted permissions — no mutation.

### 2.9 Error wrapping pattern

Every backend wraps its library's exceptions at the call site, preserving the chain:

```python
try:
    raw_member = self._zf.getinfo(name)
except KeyError as exc:
    raise ReadError(f"Member '{name}' not found", format=ArchiveFormat.ZIP) from exc
except zipfile.BadZipFile as exc:
    raise CorruptionError("ZIP central directory corrupt", format=ArchiveFormat.ZIP) from exc
```

This pattern means:
- `except ArchiveyError` catches all library errors uniformly.
- `except Exception` still shows the original traceback via `__cause__`.
- No internal library exception leaks to the caller.

A `@translate_errors(format)` decorator handles the common case:

```python
@translate_errors(ArchiveFormat.ZIP)
def open_member(self, member: Member) -> BinaryIO:
    return self._zf.open(member.original_name)
```

### 2.10 Cost Receipt computation

Each backend computes its `CostReceipt` in `open_read()`, **before** any heavy I/O:

```
ZIP backend:
  → reads central directory (already required to open the ZIP)
  → ListingCost.O1 (EOCD parsed)
  → AccessCost.DIRECT (each member has an offset in central dir)
  → StreamCapability.SEEKABLE (zipfile required seek)

TAR.GZ backend:
  → ListingCost.ON (no central dir; must stream)
  → AccessCost.SOLID (gzip is a single stream)
  → StreamCapability.SEEKABLE or REPLAY_ONLY depending on source

7z backend (py7zr):
  → reads header block (fast, at start of file)
  → ListingCost.O1
  → AccessCost.SOLID if folder_count > 0 with multiple members per folder
  → solid_block_count = len(archive.solid_units)
```

### 2.11 Optional dependencies and graceful degradation

```python
# backends/_7z.py
try:
    import py7zr
    _PY7ZR_AVAILABLE = True
except ImportError:
    _PY7ZR_AVAILABLE = False

class SevenZBackend(Backend):
    OPTIONAL_DEPENDENCY = "py7zr"
    FORMAT = ArchiveFormat.SEVEN_Z

    @classmethod
    def detect(cls, peek: bytes) -> bool:
        if not _PY7ZR_AVAILABLE:
            return False   # don't claim the format if we can't handle it
        return peek[:6] == b'7z\xbc\xaf\x27\x1c'
```

When a 7z file is detected but `py7zr` is not installed, `BackendRegistry.detect_backend()` raises `UnsupportedFormatError` with the message:
> "7-Zip format detected but backend is not installed. Run: pip install archivey[7z]"

---

## 3. Data Flow Diagrams

### 3.1 Opening an archive

```
archivey.open("file.zip")
  │
  ▼
_detection.py: detect_format()
  │  peek first 4KiB
  │  match magic bytes → ArchiveFormat.ZIP
  ▼
BackendRegistry.detect_backend()
  │  find ZipBackend
  ▼
ZipBackend.open_read(source, intent, ...)
  │  zipfile.ZipFile(source)  ← reads EOCD, central directory
  │  build CostReceipt
  │  build ArchiveInfo
  ▼
ZipReader (ArchiveReader)
  │  wraps zipfile.ZipFile instance
  │  lazy member iterator
  └─► returned to caller
```

### 3.2 Sequential iteration

```
with archivey.open("archive.tar.gz") as ar:
    for member in ar:
        data = ar.read(member)
        ↑
        │
ArchiveReader.__iter__()
  └─► TarReader._iter_members()
        │  tarfile.TarFile.next() — reads one header block
        │  maps TarInfo → Member (frozen dataclass)
        └─► yield Member

ArchiveReader.read(member)
  └─► TarReader._open_member(member)
        │  tarfile.TarFile.extractfile(member.original_name)
        └─► returns BinaryIO  →  .read()
```

### 3.3 Safe extraction flow

```
archivey.extract("untrusted.zip", "/safe/dest", policy=STRICT)
  │
  ▼
archivey.open() → ZipReader
  │
  ▼
_extraction.extract_all(reader, dest, policy=STRICT, ...)
  │
  ├─► for member in reader:
  │     _filters.check_universal(member)    ← path traversal, absolute path, null byte
  │     safe_member = POLICY_TRANSFORMS[STRICT](member)   ← strip exe bits, uid/gid
  │     extract_member(safe_member, reader._open_member, dest, ...)
  │           │
  │           ├─► handle overwrite policy
  │           ├─► mkdir parents
  │           ├─► copy chunks + BombTracker.count()
  │           └─► set mtime (best-effort)
  │
  └─► second pass: create symlinks + verify resolution
```

### 3.4 Conversion pipeline

```
with archivey.open("input.tar.gz") as reader, \
     archivey.create("output.zip") as writer:
    writer.add_members(reader)
         │
         ▼
    for member in reader:
        if member.type not in writer.SUPPORTED_TYPES:
            log.warning(...)
            continue
        stream = reader.open(member)
        writer.add_stream(stream, name=member.name,
                          size=member.size, modified=member.modified,
                          mode=member.mode)
        stream.close()
```

Memory usage: one member at a time, one chunk (1 MiB) at a time. No intermediate disk spooling unless the target format requires it (e.g. ZIP needs CRC before writing local header → uses a `SpooledTemporaryFile` per member up to `spool_size`, then streams).

---

## 4. Security Architecture

### 4.1 Defense in depth for path traversal

Three independent layers:

1. **`check_universal()` on the Member** (before any I/O): purely string-based check on `member.name` after normalization. Rejects `..` components, absolute paths, null bytes.

2. **Pre-extraction path computation**: `dest / member.name` is computed and checked with `.resolve()` — verifies the resolved absolute path starts with `dest.resolve()`.

3. **Post-symlink-creation check**: after `os.symlink()`, the created link's target is re-resolved with `Path.resolve()` to detect chained symlink attacks (where earlier members created symlinks that redirect later writes).

This three-layer approach catches:
- Layer 1: obvious traversals in the name string
- Layer 2: subtle path collisions via OS-specific normalization
- Layer 3: TOCTOU symlink attacks within the archive itself

### 4.2 Bomb detection architecture

```python
class BombTracker:
    def __init__(self, max_bytes: int, max_ratio: float):
        self._max_bytes = max_bytes
        self._max_ratio = max_ratio
        self._total_bytes = 0

    def count(self, member: Member, chunk_bytes: int) -> None:
        self._total_bytes += chunk_bytes
        if self._total_bytes > self._max_bytes:
            raise ExtractionError(
                f"Extraction limit reached: {self._total_bytes} bytes > {self._max_bytes}"
            )
        if member.compressed_size and member.compressed_size > 0:
            ratio = self._total_bytes / member.compressed_size
            if ratio > self._max_ratio:
                raise ExtractionError(
                    f"Decompression ratio {ratio:.0f}:1 exceeds limit {self._max_ratio:.0f}:1"
                )
```

`BombTracker` is constructed once per `extract_all()` call and passed through to each member extraction. Total bytes are cumulative across all members.

---

## 5. Trade-off Record

### 5.1 zipfile vs third-party ZIP library

**Decision:** use stdlib `zipfile` for the core ZIP backend.

**Considered:** `zipfile38`, `python-libarchive-c`, `zipstream-new`.

**Rationale:** `zipfile` covers 99% of real-world ZIPs and has no dependencies. Its metadata handling for Unix extra fields (UID/GID, permissions) is usable. The main gaps (Zip64 edge cases, ZIP64 data descriptors) are known and can be worked around. `python-libarchive-c` would give C-speed extraction but introduces a native dependency that complicates packaging on Windows.

**If needed later:** an optional `[fast]` extra with `python-libarchive-c` could be added as a drop-in replacement backend.

### 5.2 Frozen dataclass vs attrs/pydantic for Member

**Decision:** `@dataclass(frozen=True)` from stdlib.

**Considered:** `attrs`, `pydantic`.

**Rationale:** `pydantic` adds validation (good) but is a heavy dependency and adds runtime overhead for every member construction. `attrs` is cleaner but also a dependency. Since Archivey aims for zero core dependencies, stdlib dataclass is the correct choice. Validation happens in the backend before construction, not on the model itself.

### 5.3 Sync-only API

**Decision:** v1 is synchronous only.

**Rationale:** the main backend libraries (`zipfile`, `tarfile`, `py7zr`, `rarfile`) are all blocking/synchronous. An async API on top of blocking I/O is worse than no async API — it gives the illusion of async without the benefit. If async is needed, the pattern is `asyncio.to_thread(archivey.extract, ...)`.

A future `archivey.asyncio` module using async generators is a clean add-on.

### 5.4 No appending / in-place modification

**Decision:** write is create-only; no in-place modify.

**Rationale:** ZIP append is technically possible (write a new central directory at the end) but is fragile and creates corrupt archives if interrupted. 7z has no append mode. TAR can be appended to (`a` mode) but the result is not a valid multi-stream archive. The correct workflow is "read old, write new" — the conversion pipeline makes this trivial.

### 5.5 Decompression bomb limits: defaults

`max_extracted_bytes=2 GiB`, `max_ratio=1000`:
- 2 GiB is enough for most legitimate use cases and prevents gigabyte-class bombs.
- 1000:1 is extremely generous (typical DEFLATE is 3:1 to 10:1; text compresses to maybe 20:1). Even 42.zip's outer layer reaches ~391:1. This catches pathological ratios while not triggering on legitimate very-compressible data.
- Both are caller-configurable via `extract(..., max_extracted_bytes=..., max_ratio=...)`.

---

## 6. Dependency Matrix

| Extra | Package | Version floor | Purpose |
|-------|---------|---------------|---------|
| (core) | zipfile | stdlib | ZIP read/write |
| (core) | tarfile | stdlib | TAR read/write |
| (core) | gzip, bz2, lzma | stdlib | single-file compressors |
| `[7z]` | `py7zr` | ≥0.20 | 7-Zip read/write |
| `[rar]` | `rarfile` | ≥4.0 | RAR read (+ `unrar` binary) |
| `[iso]` | `pycdlib` | ≥1.14 | ISO 9660 read |
| `[zstd]` | `zstandard` | ≥0.21 | Zstandard .zst and .tar.zst |
| `[all]` | all above | — | Everything |

Dev/test extras: `pytest`, `pytest-cov`, `mypy`, `ruff`, `hypothesis`.

---

## 7. Performance Notes

### 7.1 ZIP central directory caching

`zipfile.ZipFile` reads the central directory on `__init__`. The ZIP backend does not re-read it. Member name lookup is `O(1)` via an internal dict (`self._zf.NameToInfo`).

### 7.2 TAR sequential read

TAR backends in streaming mode (`r|gz`) read blocks of 512 bytes and yield `TarInfo` objects. They never seek backward. The Python `tarfile` module handles this internally; the backend just iterates.

For random access on a compressed TAR (`.tar.gz` etc.), there is no efficient option — the backend materializes a sorted list of `(offset, TarInfo)` tuples by doing a full streaming scan once, then uses those offsets for subsequent random access (requiring seeking in the decompressed stream — only possible for plain `.tar` without compression wrapper). For compressed TARs, random access requires decompressing from the start each time — this is reported via `AccessCost.SOLID`.

### 7.3 7z solid block optimization

When iterating a 7z archive sequentially, `py7zr` decompresses each solid block once. The `SevenZReader._iter_members()` implementation passes members to the caller in solid-block order, which naturally gives `O(blocks)` total decompression cost for sequential access.

For random access across solid blocks, `py7zr` must re-decompress preceding blocks. The `CostReceipt` communicates this cost explicitly.

### 7.4 Chunk size for extraction

Default chunk size is 1 MiB (1 048 576 bytes). This is a balance between:
- Too small: excessive system call overhead.
- Too large: excessive peak memory usage.

The chunk size is passed through to `shutil.copyfileobj(src, dst, length=CHUNK_SIZE)`.
