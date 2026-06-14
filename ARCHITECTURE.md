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
├── fixtures/              # Committed binary archives — only what can't be generated
│   ├── adversarial/       # Hand-crafted: path traversal, zip bombs, corrupt headers
│   ├── external/          # Archives requiring specific tools/OS (Windows junctions, etc.)
│   └── *.json             # Sidecar per committed archive (expected member list)
├── create_adversarial.py  # Script that (re)generates adversarial fixtures
├── sample_archives.py     # Declarative specs: ArchiveContents, FileInfo, ArchiveCreationInfo
├── create_archives.py     # Generates archives from specs into tmp_path / cache dir
├── conftest.py            # pytest_generate_tests, sample_archive_path fixture
├── test_detection.py
├── test_types.py
├── test_zip.py
├── test_tar.py
├── test_single.py
├── test_7z.py
├── test_rar.py
├── test_iso.py
├── test_extraction.py     # filter/security tests; uses adversarial fixtures
├── test_writing.py
├── test_conversion.py
├── test_equivalence.py    # equivalence matrix across formats
└── test_patterns.py       # sample usage patterns (hashing, link-following, conversion)
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

The methods backends **must or may** implement:
```python
# --- Class-level attributes (set once per backend class, not per instance) ---

_SUPPORTS_RANDOM_ACCESS: bool = True
# Set to False for inherently sequential formats (plain .tar on a non-seekable stream).
# The ABC reads this to decide whether to allow open() and extract().

_MEMBER_LIST_UPFRONT: bool = True
# Set to True if the format has a central directory (ZIP, 7z) so get_members() is cheap.
# Set to False for streaming formats (TAR) where listing requires reading the whole archive.

# --- Required abstract methods ---

@abstractmethod
def _iter_members(self) -> Iterator[Member]: ...
# Yield Member objects in archive order, metadata only.
# Called once by the base class to populate the member registry.
# Store any backend-specific data needed by _open_member in member.raw_info.

@abstractmethod
def _open_member(self, member: Member) -> BinaryIO: ...
# Return a raw data stream for member. No link following.
# May use internal caching (e.g. 7z folder cache).
# Called only for members where member.is_file is True.

@abstractmethod
def _close_archive(self) -> None: ...
# Release backend resources (file handles, temp dirs). Called once by close().

# --- Optional overrides ---

def _iter_with_data(self) -> Iterator[tuple[Member, BinaryIO]]:
    # Default: naive — calls _open_member() per file member.
    # Correct for non-solid formats (ZIP, TAR, GZ): no extra cost.
    # Solid-archive backends MUST override for bounded memory:
    #   SevenZReader: folder by folder, release before moving to next.
    #   RarReader (solid): single unrar pass, tmpdir freed in finally.
    for member in self._iter_members():
        if member.is_file:
            yield member, self._open_member(member)
        else:
            yield member, None
```

`open()` and `read()` in the ABC add link-following on top of `_open_member()`:
```python
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

def stream_members(self) -> Iterator[tuple[Member, BinaryIO]]:
    return self._iter_with_data()
```

`add_members()` in `ArchiveWriter` calls `reader.stream_members()` so conversions always take the bounded-memory path.

Everything else (`__iter__`, `__getitem__`, `read`, `open`, `stream_members`, `extract`, `extract_all`, `members`) is implemented once in the ABC.

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

`_extraction.py` implements the safe extraction coordinator. It has no deferred/pending state; both streaming and random-access extraction use the same unified forward pass driven by `_iter_with_data()`.

```python
class ExtractionCoordinator:
    def __init__(self, dest: Path, policy: ExtractionPolicy,
                 overwrite: OverwritePolicy, bomb_tracker: BombTracker): ...

    def run(
        self,
        members: Iterable[Member],
        open_fn: Callable[[Member], BinaryIO],
        hardlink_sources: dict[int, Member],  # member_id → source member (pre-built)
    ) -> dict[Path, Member]:
        """Single forward pass. Works identically in streaming and random-access mode."""
```

The `hardlink_sources` map is built before the pass starts by scanning the full member list (available in random-access mode) or the upcoming-members list where possible. It tells the coordinator: "this member's data will be needed for N hardlinks that follow it — make sure its path is recorded."

During the pass:
- **FILE / DIR / SYMLINK**: write immediately. Record `member_id → extracted_path`.
- **HARDLINK**: if source `extracted_path` is already recorded, `os.link` it; otherwise `shutil.copy2`. In streaming mode, TAR guarantees target precedes link — if the source was filtered out, that's an explicit error with a clear message. In random-access mode, if the source wasn't selected by the filter, it's added to the extraction set implicitly (marked "data needed, discard after linking").
- After the pass: apply mtime/permissions to all extracted paths (best-effort, single `os.utime` / `os.chmod` loop).

This replaces the previous `ExtractionHelper` class and its pending/deferred state machine. No move-vs-link signaling. No `can_move_file` flag. No `pending_target_members_by_source_id` dict.

### 2.7 Symlinks: single-pass with post-creation check

Symlinks don't need a second pass. They are written in archive order as encountered. After creating each symlink:

```
os.symlink(link_target, dest_path)
resolved = (dest_path.parent / link_target).resolve()
if not resolved.is_relative_to(dest.resolve()):
    dest_path.unlink()
    raise FilterRejectionError(...)
```

This is simpler than deferred creation and catches escapes immediately rather than at the end of the run. It is safe because symlink creation is atomic on POSIX; the escape check happens before any follow-through reads.

The one edge case is a symlink whose target is a *later* member in the same archive (rare in practice). In streaming mode, this is treated the same as an escaped symlink — rejected with a clear error. In random-access mode, the check is deferred until all members are written (same final verification). The default `DATA` extraction filter already rejects most such patterns.

### 2.8 Test architecture

Tests are split into two tiers:

**Tier 1 — generated-on-demand** (the vast majority): archive content is declared as Python `ArchiveContents` / `FileInfo` specs, generated at test time into a per-session cache directory (keyed by a hash of the spec + creation parameters), and never committed to the repo. The `conftest.py` fixture handles generation and caching transparently.

```python
@pytest.mark.sample_archives(container=ContainerFormat.ZIP, configs=["default", "altlibs"])
def test_read_basic(sample_archive: SampleArchive, archivey_config):
    with open_archive(sample_archive.get_archive_path(), config=archivey_config) as ar:
        assert ar.get_members() == sample_archive.contents.expected_members()
```

The `sample_archive.contents` object is both the generation spec and the ground truth — no JSON needed for generated archives. Format-specific feature flags (`ArchiveFormatFeatures`) tell the assertion helper which fields to compare (e.g. rounded mtimes for `zipfile`-generated ZIPs, no dir entries for py7zr).

**Tier 2 — committed fixtures with JSON sidecars** (a small set, committed to `tests/fixtures/`):

- Archives that require a specific OS or unavailable tool to generate (Windows junctions, RAR created with exact version flags, malformed-but-valid-in-the-wild archives).
- Adversarial archives: hand-crafted zip bombs, path traversal attempts, corrupt headers. These are small binary files; committing them is cheap and they rarely change.
- For every committed archive `foo.rar`, a sidecar `foo.json` documents the expected member list. A single parametrized test `test_fixtures.py::test_committed_fixture` runs all of them.

```json
{
  "format": "RAR5",
  "members": [
    {"name": "dir/", "type": "DIR", "size": 0},
    {"name": "dir/file.txt", "type": "FILE", "size": 42,
     "mtime": "2023-01-15T12:00:00+00:00"}
  ]
}
```

**Cross-tool verification**: For any archive parseable by `7z l -slt` or `unrar lt`, CI can run these and compare the output against the parsed `Member` fields. This is implemented as an optional pytest plugin (`--verify-with-7z`) so it doesn't require tool installation in all environments.

### 2.10 Filters as pure transform functions

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

### 2.11 Error wrapping pattern

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

### 2.12 Cost Receipt computation

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

### 2.13 Optional dependencies and graceful degradation

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

### 5.6 py7zr wrapper vs custom streaming 7z reader

**Decision (v1):** use `py7zr` with lazy per-folder caching.

**The limitation:** py7zr is a push-based API with no streaming pull. The caching approach buffers each solid block (in memory or spooled to disk). For archives with very large solid blocks, peak memory/disk could be significant.

**The alternative — custom streaming reader.** A custom 7z reader could give true pull-based streaming with no per-block buffering:

```
fold the compressed folder stream → lzma.LZMADecompressor → 
  read exactly file_0.uncompressed_size bytes → yield as file_0 stream →
  read exactly file_1.uncompressed_size bytes → yield as file_1 stream →
  ...
```

This is architecturally feasible because:
- py7zr's `archiveinfo.py` header parser (1156 lines) is self-contained and importable directly.
- LZMA2 is implemented via Python's stdlib `lzma.LZMADecompressor` inside py7zr.
- The BCJ filter is a C extension (`bcj` package) — still a dependency, but a small one.
- The decompressed stream per folder is byte-contiguous: files are laid out sequentially in the decompressed output, and sizes are known from the header.

The main implementation challenges: correctly driving `lzma.LZMADecompressor` in streaming mode across chunk boundaries, and handling all codec IDs (LZMA2, LZMA1, Deflate, BZip2, Delta, BCJ variants). The latter is manageable since py7zr already maps codec IDs to decompressor chains.

**Recommendation:** Implement the caching approach for v1 (1–2 days of work). Mark the custom streaming reader as a Phase 2 item under a `[7z-native]` extra — it replaces the py7zr backend with a streaming one, behind the same `ArchiveReader` ABC.

### 5.7 rarfile wrapper vs custom unrar invocation

**Decision (v1):** use `rarfile` for listing/metadata, but bypass its extraction for solid archives by invoking `unrar x` directly.

**Rationale:** rarfile's `extractall()` spawns N subprocesses for N files. Running `unrar x archive.rar destdir/` once is strictly better for solid archives and requires only one extra `subprocess.run` call. rarfile's tool-detection machinery (`tool_setup()`) is reused to locate the correct binary.

**The alternative — `python-libarchive-c`.** Investigated; libarchive's RAR backend explicitly does not support solid archives. Not viable.

**Rolling a RAR reader** is not practical: the RAR format is proprietary and documented only through reverse engineering. The reference implementation is the `unrar` tool itself.

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

### 7.3 7z backend — lazy per-folder caching

py7zr's push model (`extract(factory=...)`) and the solid block problem are handled entirely inside `SevenZReader._open_member()` via lazy per-folder caching. The public API (`for member in ar: ar.open(member)`) requires no changes.

**Folder-to-file mapping** (confirmed via py7zr internals):
- Each `FileInfo` dict has a `"folder"` key → reference to the `Folder` object it belongs to.
- Files in the same solid block share the same `Folder` instance (object identity, not an index).
- `SubstreamsInfo.num_unpackstreams_folders[i]` gives file count per folder.

**`_open_member()` — lazy folder cache (monotonic memory):**
```python
def _open_member(self, member: Member) -> BinaryIO:
    name = member.original_name
    folder = self._file_info_map[name]["folder"]

    if folder not in self._folder_cache:
        targets = [fi["filename"] for fi in folder.files]
        class BlockFactory(py7zr.WriterFactory):
            def create(inner_self, fn):
                spooled = tempfile.SpooledTemporaryFile(max_size=64 << 20)
                self._folder_cache[folder][fn] = spooled   # written into permanent cache
                return _Py7zIOAdapter(spooled)
        self._folder_cache[folder] = {}
        self._sz.extract(targets=targets, factory=BlockFactory())
        self._sz.reset()   # required before next extraction

    buf = self._folder_cache[folder][name]
    buf.seek(0)
    return buf
```

First `open()` for any member in a folder pays O(folder_decompression). All subsequent calls to the same folder are O(1). Cache entries accumulate until `close()` — memory grows with the number of distinct folders accessed.

**`_iter_with_data()` — folder-by-folder, bounded memory:**
```python
def _iter_with_data(self) -> Iterator[tuple[Member, BinaryIO]]:
    for folder in self._folders:
        targets = [fi["filename"] for fi in folder.files]
        folder_bufs: dict[str, SpooledTemporaryFile] = {}

        class FolderFactory(py7zr.WriterFactory):
            def create(inner_self, fn):
                spooled = tempfile.SpooledTemporaryFile(max_size=64 << 20)
                folder_bufs[fn] = spooled
                return _Py7zIOAdapter(spooled)

        self._sz.extract(targets=targets, factory=FolderFactory())
        self._sz.reset()

        for fi in folder.files:
            member = self._member_map[fi["filename"]]
            buf = folder_bufs[fi["filename"]]
            buf.seek(0)
            yield member, buf

        # folder_bufs goes out of scope here → GC releases SpooledTemporaryFiles
        # Peak memory = size of the largest single folder
```

**`_Py7zIOAdapter`** adapts `SpooledTemporaryFile` to the `Py7zIO` interface. Must be seekable — py7zr calls `seek(0)` after writing as a final rewind; it never reads back.

**`_iter_members()`** is pure metadata: calls `sz.list()`, O(1), no decompression.

### 7.4 RAR backend — one-shot extraction for solid archives

rarfile has a critical limitation: `extractall()` does **not** run `unrar` once for all files — it calls `open()` per file, spawning a separate subprocess each time. For solid archives, each subprocess re-processes the full archive. This is O(N) subprocess invocations, each doing O(archive_size) work.

**`_open_member()` — solid cache persists until `close()`:**
```python
def _open_member(self, member: Member) -> BinaryIO:
    if not self._is_solid:
        return self._rf.open(member.original_name)   # rarfile's hack, efficient
    self._ensure_solid_cache()                        # runs unrar x once, lazy
    return open(self._solid_cache_dir / member.name, 'rb')

def _ensure_solid_cache(self):
    if self._solid_cache_dir is not None:
        return
    self._solid_cache_dir = Path(tempfile.mkdtemp())
    setup = rarfile.tool_setup()
    cmd = [setup._unrar_tool, 'x', '-inul',
           f'-p{self._password}' if self._password else '-p-',
           str(self._path), str(self._solid_cache_dir) + os.sep]
    subprocess.run(cmd, check=True)
```

(`x` preserves relative paths; `e` flattens — we need `x` to avoid name collisions.)

`_solid_cache_dir` is cleaned up in `close()`. Disk persists for the archive's lifetime.

**`_iter_with_data()` — solid RAR, disk freed early:**
```python
def _iter_with_data(self) -> Iterator[tuple[Member, BinaryIO]]:
    if not self._is_solid:
        yield from super()._iter_with_data()   # default: open() per member
        return
    tmpdir = Path(tempfile.mkdtemp())
    try:
        setup = rarfile.tool_setup()
        subprocess.run([setup._unrar_tool, 'x', '-inul', ...], check=True)
        for member in self._iter_members():
            yield member, open(tmpdir / member.name, 'rb')
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
```

Both paths run `unrar` exactly once. The distinction is lifetime: `_open_member()`'s cache persists until `close()`; `_iter_with_data()`'s tmpdir is cleaned up when the `stream_members()` loop ends (via the `finally` block in the generator, triggered by `close()` on the iterator).

**`unrar e` vs `unrar x`:** `e` flattens to a single directory; `x` preserves paths. We use `x` to avoid name collisions. The command is built from rarfile's `tool_setup()` to respect any user-configured tool path.

**Non-solid RAR:** rarfile's per-file hack is used. For files under 20 MiB, it creates a mini-archive with just the target's compressed data and runs `unrar` on that — O(member_size) per call.

### 7.5 Chunk size for extraction

Default chunk size is 1 MiB (1 048 576 bytes). This is a balance between:
- Too small: excessive system call overhead.
- Too large: excessive peak memory usage.

The chunk size is passed through to `shutil.copyfileobj(src, dst, length=CHUNK_SIZE)`.

---

## 8. Link-Following in ArchiveReader

Three archive formats handle links differently at the library level:

| Format | hardlink `open()` | symlink `open()` |
|--------|------------------|-----------------|
| TAR | tarfile follows automatically — `extractfile()` returns data of linked file | tarfile follows automatically |
| RAR5 | rarfile follows `RAR5_XREDIR_HARD_LINK` and `FILE_COPY` automatically | rarfile returns target path as bytes (link not followed) |
| ZIP | no hardlink concept | symlink stored as regular file with target path as content |
| 7z | no hardlink concept | symlink stored with metadata; content is target path |

The ABC layer adds uniform link-following on top, catching the ZIP/7z/RAR symlink cases. Backends that already follow links internally (TAR hardlinks, RAR5 hardlinks) do so at a lower level — the ABC-level check is a no-op for those (the result is already the target's data, not the link path).

The `_depth` guard (`max=8`) in the ABC `open()` prevents symlink cycles within the archive from causing infinite recursion.
