# Archivey — Detailed Technical Specification

> This document formalizes and extends the high-level requirements into a precise, implementable contract. It is the authoritative reference for API design, data model shape, behavior guarantees, and security semantics.

---

## 1. Scope and Purpose

Archivey is a Python library for reading, streaming, and safely extracting archives through a single, uniformly-typed interface. It presents ZIP, TAR (all variants), RAR, 7z, ISO 9660, plain directories, and single-file compressed streams as first-class, interchangeable objects.

**Design authority:** when a format quirk cannot be cleanly mapped to the unified model, the library surfaces the inconsistency as an explicit, documented field value (`None` or an `Unknown` sentinel) — never as a silent guess, default, or exception.

---

## 2. Target Environment

| Item | Constraint |
|------|------------|
| Python version | 3.11+ |
| Core dependencies | None (stdlib only) |
| Optional extras | `[7z]`, `[rar]`, `[iso]`, `[zstd]`, `[all]` |
| OS support | Linux, macOS, Windows |
| Thread safety | Readers are not thread-safe. One reader per thread. Writers are not thread-safe. |

---

## 3. Public API Surface

### 3.1 Top-level functions

```python
# Open an archive for reading
archivey.open(
    source: str | Path | BinaryIO,
    *,
    format: ArchiveFormat | None = None,  # override detection
    intent: Intent = Intent.AUTO,
    password: str | bytes | None = None,
    encoding: str = "utf-8",             # fallback for legacy non-unicode paths
) -> ArchiveReader

# Create a new archive for writing
archivey.create(
    dest: str | Path | BinaryIO,
    format: ArchiveFormat,
    *,
    compression: CompressionSpec | None = None,
    password: str | bytes | None = None,
    encoding: str = "utf-8",
) -> ArchiveWriter

# One-shot extraction (most common use case)
archivey.extract(
    source: str | Path | BinaryIO,
    dest: str | Path,
    *,
    members: Iterable[str | Member] | None = None,  # None = all
    policy: ExtractionPolicy = ExtractionPolicy.STRICT,
    overwrite: OverwritePolicy = OverwritePolicy.ERROR,
    format: ArchiveFormat | None = None,
    password: str | bytes | None = None,
    on_progress: Callable[[ExtractionProgress], None] | None = None,
) -> list[ExtractionResult]

# Detect format without opening
archivey.detect_format(
    source: str | Path | BinaryIO,
) -> FormatInfo
```

### 3.2 ArchiveReader

```python
class ArchiveReader:
    # --- Metadata ---
    @property
    def info(self) -> ArchiveInfo: ...
    @property
    def cost(self) -> CostReceipt: ...
    @property
    def format(self) -> ArchiveFormat: ...

    # --- Member iteration ---
    def __iter__(self) -> Iterator[Member]: ...           # sequential, in-order
    def members(self) -> list[Member]: ...               # materializes all (may trigger scan)
    def __len__(self) -> int: ...                        # may trigger scan
    def __contains__(self, name: str) -> bool: ...

    # --- Random access (requires seekable source or RANDOM intent) ---
    def __getitem__(self, name: str) -> Member: ...      # KeyError if absent
    def get(self, name: str, default=None) -> Member | None: ...

    # --- Data access ---
    def read(self, member: str | Member) -> bytes: ...
    def open(self, member: str | Member) -> BinaryIO: ...   # streaming; caller must close

    # --- Extraction helpers (delegates to archivey.extract internals) ---
    def extract(
        self,
        member: str | Member,
        dest: str | Path,
        *,
        policy: ExtractionPolicy = ExtractionPolicy.STRICT,
        overwrite: OverwritePolicy = OverwritePolicy.ERROR,
    ) -> Path: ...

    def extract_all(
        self,
        dest: str | Path,
        *,
        policy: ExtractionPolicy = ExtractionPolicy.STRICT,
        overwrite: OverwritePolicy = OverwritePolicy.ERROR,
        on_progress: Callable[[ExtractionProgress], None] | None = None,
    ) -> list[ExtractionResult]: ...

    # --- Context manager ---
    def __enter__(self) -> ArchiveReader: ...
    def __exit__(self, *_) -> None: ...
    def close(self) -> None: ...
```

**Constraint:** calling `__getitem__`, `get`, or random `extract` on a reader opened with `Intent.SEQUENTIAL` raises `UnsupportedOperationError` unless the backend can satisfy it cheaply (e.g. the archive has an in-memory index already loaded).

**Efficiency guarantee:** Calling `open()` or `read()` on members during sequential `__iter__` must not trigger more than O(solid_blocks) total decompression passes for formats with solid archives (7z, RAR). Concretely: iterating N members of a solid 7z archive must not run decompression N times. The backend achieves this via internal per-solid-block caching (see §10.4 and §10.5). No special iteration method is needed; the standard `for member in ar: ar.open(member)` pattern is efficient by design.

**Link following:** `open()` and `read()` transparently follow symlinks and hardlinks that point to other members in the same archive. If `member.type` is `SYMLINK` or `HARDLINK`, the call is redirected to `open(reader[member.link_target])`. If the link target is not present in the archive, `ReadError` is raised. If the link target itself is a link, it is followed recursively up to a maximum depth of 8 (beyond which `ReadError` is raised to prevent cycles). This behavior is format-independent and is implemented once in the `ArchiveReader` ABC — it does not rely on format-level link resolution (e.g. rarfile follows RAR5 hardlinks internally; our layer handles the remaining cases).

### 3.3 ArchiveWriter

```python
class ArchiveWriter:
    def add(
        self,
        source: str | Path,
        *,
        name: str | None = None,       # override archive path
        recursive: bool = True,
        compression: CompressionSpec | None = None,
    ) -> None: ...

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

    def add_member(self, member: Member, data: BinaryIO) -> None: ...

    def add_members(
        self,
        reader: ArchiveReader,
        *,
        members: Iterable[Member] | None = None,
    ) -> None: ...

    def __enter__(self) -> ArchiveWriter: ...
    def __exit__(self, *_) -> None: ...
    def close(self) -> None: ...
```

`add_members` is the conversion primitive: it streams data directly from reader to writer without intermediate buffering. The writer may buffer internally per its format requirements (e.g. ZIP local headers need the CRC before writing), but must not buffer the full archive.

---

## 4. Data Model

### 4.1 ArchiveFormat

```python
class ArchiveFormat(Enum):
    ZIP       = "zip"
    TAR       = "tar"         # bare .tar
    TAR_GZ    = "tar.gz"
    TAR_BZ2   = "tar.bz2"
    TAR_XZ    = "tar.xz"
    TAR_ZST   = "tar.zst"    # requires [zstd] extra
    TAR_LZ4   = "tar.lz4"    # requires [lz4] extra
    GZ        = "gz"          # single-file gzip
    BZ2       = "bz2"
    XZ        = "xz"
    ZST       = "zst"
    SEVEN_Z   = "7z"          # requires [7z] extra
    RAR       = "rar"         # requires [rar] extra
    ISO       = "iso"         # requires [iso] extra
    DIRECTORY = "directory"   # plain filesystem directory
```

### 4.2 MemberType

```python
class MemberType(Enum):
    FILE      = "file"
    DIRECTORY = "directory"
    SYMLINK   = "symlink"       # includes Windows junction (flagged via extra["is_junction"])
    HARDLINK  = "hardlink"
    OTHER     = "other"         # device nodes, FIFOs, sockets — extraction always rejected
```

### 4.3 CompressionAlgo and CompressionMethod

```python
class CompressionAlgo(Enum):
    STORED   = "stored"
    DEFLATE  = "deflate"
    DEFLATE64 = "deflate64"
    BZIP2    = "bzip2"
    LZMA     = "lzma"
    LZMA2    = "lzma2"
    ZSTD     = "zstd"
    LZ4      = "lz4"
    PPMD     = "ppmd"
    BCJ      = "bcj"           # x86 executable filter
    BCJ2     = "bcj2"
    DELTA    = "delta"
    UNKNOWN  = "unknown"       # unrecognized codec ID

@dataclass(frozen=True)
class CompressionMethod:
    algo: CompressionAlgo
    level: int | None = None        # compression level if known
    properties: bytes | None = None # raw codec properties blob
```

A `tuple[CompressionMethod, ...]` models a filter chain (e.g., `(CompressionMethod(BCJ2), CompressionMethod(LZMA2))` for a typical 7z executable entry).

### 4.4 Member

```python
@dataclass(frozen=True)
class Member:
    # --- Type ---
    type: MemberType

    # --- Identity ---
    sequence: int                           # 0-based position in source archive
    name: str                               # normalized: forward slashes; trailing / for dirs; no leading /
    original_name: str                      # verbatim bytes decoded with archive encoding

    # --- Sizes (None if format cannot provide) ---
    size: int | None                        # uncompressed size in bytes
    compressed_size: int | None

    # --- Timestamps ---
    # timezone-aware if format records UTC or a UTC offset; naive if local wall-clock
    modified: datetime | None
    accessed: datetime | None
    created: datetime | None

    # --- Permissions & ownership ---
    mode: int | None                        # low 12 bits: standard Unix permission bits
    uid: int | None
    gid: int | None
    uname: str | None
    gname: str | None

    # --- Link semantics ---
    link_target: str | None                 # SYMLINK or HARDLINK target path (not normalized)

    # --- Compression ---
    compression: tuple[CompressionMethod, ...] = ()

    # --- Flags ---
    is_encrypted: bool = False
    is_sparse: bool = False                 # TAR sparse files; extraction as regular file

    # --- Integrity ---
    crc32: int | None = None

    # --- Format-specific overflow ---
    # Keys are namespaced: "zip.extra_fields", "tar.pax_headers", "iso.rock_ridge", etc.
    # Excluded from __hash__ and __eq__: format-specific extras don't affect logical identity.
    extra: dict[str, Any] = field(default_factory=dict, hash=False, compare=False)
```

**Normalization rules for `name`:**
1. Replace all `\` with `/`.
2. Strip leading `/` and `./`.
3. Collapse `//` and `foo/../bar` sequences.
4. Append `/` for directories if not present.
5. Never produce an empty string — root dir becomes `"."`.

If normalization would change the meaning of the path (e.g. collapse produces a different logical path), the original is still preserved in `original_name` and a warning is emitted via the standard `logging` module under logger `archivey.normalization`.

### 4.5 ArchiveInfo

```python
@dataclass(frozen=True)
class ArchiveInfo:
    format: ArchiveFormat
    format_version: str | None        # e.g. "4.5" for ZIP, "5" for RAR5
    is_solid: bool
    member_count: int | None          # None if requires full scan to determine
    comment: str | None
    is_encrypted: bool                # header encryption (7z, RAR5)
    is_multivolume: bool
    cost: CostReceipt
```

### 4.6 CostReceipt

```python
class ListingCost(Enum):
    O1  = "o1"   # central directory / index present; O(1) regardless of archive size
    ON  = "on"   # no index; must scan entire stream to enumerate members

class AccessCost(Enum):
    DIRECT = "direct"   # random access to any member without reading others
    SOLID  = "solid"    # decompressing member N requires decompressing members 0..N-1

class StreamCapability(Enum):
    SEEKABLE     = "seekable"       # source supports arbitrary seeking
    REPLAY_ONLY  = "replay_only"    # non-seekable; rewinding is impossible

@dataclass(frozen=True)
class CostReceipt:
    listing_cost: ListingCost
    access_cost: AccessCost
    stream_capability: StreamCapability
    is_solid: bool
    solid_block_count: int | None   # 7z: number of solid blocks (each requires one pass)
    notes: tuple[str, ...] = ()     # human-readable caveats
```

### 4.7 FormatInfo (detection result)

```python
@dataclass(frozen=True)
class FormatInfo:
    format: ArchiveFormat
    confidence: float               # 0.0–1.0; magic match = 1.0; extension-only = 0.3
    detected_by: str                # "magic", "extension", "content_probe"
    encoding_hint: str | None       # suggested encoding for legacy path fields
```

---

## 5. Enums and Policies

### 5.1 Intent

```python
class Intent(Enum):
    AUTO       = "auto"       # library chooses optimal access mode
    SEQUENTIAL = "sequential" # caller promises forward-only iteration; disables index loading
    RANDOM     = "random"     # caller needs random access; library fails fast if impossible
```

### 5.2 ExtractionPolicy

```python
class ExtractionPolicy(Enum):
    STRICT   = "strict"    # default; untrusted archives
    STANDARD = "standard"  # moderate trust; e.g. your own older archives
    TRUSTED  = "trusted"   # bypass permission/ownership checks; path safety still enforced
```

Policy semantics (see §7 for full filter contract):

| Check | STRICT | STANDARD | TRUSTED |
|-------|--------|----------|---------|
| Path traversal reject | **always** | **always** | **always** |
| Absolute path reject | **always** | **always** | **always** |
| Symlink outside-root reject | **always** | **always** | **always** |
| Special file (device/FIFO) reject | yes | yes | yes |
| Executable bit strip | yes | no | no |
| Setuid/setgid/sticky strip | yes | yes | no |
| Ownership (uid/gid) strip | yes | no | no |
| Permission normalize to 644/755 | yes | no | no |

### 5.3 OverwritePolicy

```python
class OverwritePolicy(Enum):
    ERROR   = "error"   # raise ExtractionError if destination file exists
    SKIP    = "skip"    # silently skip existing files
    REPLACE = "replace" # overwrite unconditionally
```

---

## 6. Exception Hierarchy

```
ArchiveyError(Exception)
├── OpenError                   # cannot open / parse the archive header
│   ├── FormatDetectionError    # could not detect format
│   └── UnsupportedFormatError  # format detected but no backend available
├── ReadError                   # error reading a member
│   ├── CorruptionError         # CRC mismatch, bad data block
│   ├── TruncatedError          # unexpected EOF
│   └── EncryptionError         # password required or wrong password
├── WriteError                  # error writing an archive
├── ExtractionError             # error extracting a member to disk
│   └── FilterRejectionError    # safety filter blocked the member
│       ├── PathTraversalError  # ../ or absolute path
│       ├── SymlinkEscapeError  # symlink resolves outside dest
│       └── SpecialFileError    # device node, FIFO, socket
└── UnsupportedOperationError   # e.g. random access on sequential reader
```

**Requirement:** every `ArchiveyError` must carry:
- `message: str` — human-readable explanation
- `source_format: ArchiveFormat | None`
- `member_name: str | None` — the member being processed, if applicable
- `__cause__` — the original exception (preserved via `raise ... from ...` or `raise ... from exc` pattern)

The original traceback must be attached and surfaced by default `traceback.print_exc()` calls. Libraries must never swallow the original exception.

---

## 7. Extraction Filter Contract

### 7.1 Universal constraints (cannot be bypassed, including TRUSTED policy)

1. **Path traversal:** Any `name` component equal to `..` after splitting on `/` → `PathTraversalError`.
2. **Absolute paths:** `name` starting with `/` or a Windows drive letter (`C:\`, `\\`) → `PathTraversalError`.
3. **Null bytes:** `name` containing `\x00` → `PathTraversalError`.
4. **Symlink escape:** For SYMLINK members, resolve the target relative to the eventual extraction path. If resolution escapes the `dest` root (after fully resolving all symlink chains) → `SymlinkEscapeError`. This check is re-validated at extraction time, not just at planning time.
5. **Hardlink escape:** For HARDLINK members, the link target path must resolve within `dest` → `SymlinkEscapeError`.
6. **MemberType.OTHER:** Device nodes, FIFOs, sockets — always rejected with `SpecialFileError`, regardless of policy.

### 7.2 Policy-specific transforms

Applied **after** universal checks pass:

**STRICT:**
- Remove uid/gid (extract as current user).
- Strip all setuid (0o4000), setgid (0o2000), sticky (0o1000) bits.
- Strip execute bits on files: `mode & ~0o111`.
- Normalize remaining permissions: files → `min(mode & 0o666, 0o644)`, dirs → `0o755`.
- If `mode` is `None`, use `0o644` for files and `0o755` for directories.

**STANDARD:**
- Remove uid/gid.
- Strip setuid and setgid bits.
- If `mode` is `None`, use `0o644`/`0o755`.
- Execute bits preserved.

**TRUSTED:**
- Apply `mode` as-is.
- Apply uid/gid if running as root; otherwise skip silently.

### 7.3 Decompression bomb detection

All extraction paths must track cumulative bytes written and raise `ExtractionError` when:
- Total bytes written exceeds `max_extracted_bytes` (default: 2 GiB; caller-configurable).
- Decompression ratio for a single member exceeds `max_ratio` (default: 1000:1; caller-configurable).

These limits apply only during `extract` / `extract_all`. `read()` and `open()` return raw data and leave bomb detection to the caller.

---

## 8. Format Detection

### 8.1 Algorithm

1. Read up to `DETECTION_LIMIT` bytes (default 4 096 bytes) from the source.
2. Match against the magic-byte table (exact offsets, no heuristics).
3. On a match: return `FormatInfo(confidence=1.0, detected_by="magic")`.
4. On no match: attempt extension-based guess if source is a `Path`; return `confidence=0.3, detected_by="extension"`.
5. On conflict between magic and extension: magic wins; a `logging.WARNING` is emitted.
6. The first `DETECTION_LIMIT` bytes are **never** discarded — seekable streams are `seek(0)`'d back; non-seekable streams use a `PeekableStream` wrapper that replays the buffered bytes transparently.

### 8.2 Magic byte table

| Format | Offset | Magic bytes |
|--------|--------|-------------|
| ZIP (standard/data descriptor/empty) | 0 | `50 4B 03 04` / `50 4B 07 08` / `50 4B 05 06` |
| GZip | 0 | `1F 8B` |
| BZip2 | 0 | `42 5A 68` |
| XZ | 0 | `FD 37 7A 58 5A 00` |
| Zstandard | 0 | `28 B5 2F FD` |
| 7-Zip | 0 | `37 7A BC AF 27 1C` |
| RAR 4.x | 0 | `52 61 72 21 1A 07 00` |
| RAR 5.x | 0 | `52 61 72 21 1A 07 01 00` |
| ISO 9660 | 32 769 | `43 44 30 30 31` ("CD001") — requires ≥ 32 774 bytes peek |
| TAR (POSIX/GNU) | 257 | `75 73 74 61 72` ("ustar") — requires ≥ 512 bytes peek |
| LZ4 | 0 | `04 22 4D 18` |

**ISO caveat:** ISO detection requires reading past the default 4 KiB limit. Detection raises `FormatDetectionError` if the stream is shorter than 32 774 bytes, or explicitly returns `None` if the ISO magic is not found at sector 16 but the stream is long enough. When ISO format is suspected from extension `.iso`, the detection limit is temporarily raised to 32 774 bytes.

### 8.3 Non-seekable stream handling

`PeekableStream` wraps a non-seekable binary stream:
- Buffers the first `DETECTION_LIMIT` bytes in memory.
- Exposes a `.peek(n)` method returning buffered bytes without consuming them.
- After format detection, the stream is transparently rewound (the backend reads from the buffer, then from the underlying stream once the buffer is exhausted).
- `PeekableStream` is a `BinaryIO`-compatible object passed through to the backend.

---

## 9. Backend Registry

### 9.1 Registration

Backends register themselves via `archivey.backends.register(BackendClass)`. Core backends are registered at import time. Optional backends register themselves inside their `try/except ImportError` guard.

```python
# Internal API
class BackendRegistry:
    def register(self, backend_cls: type[Backend]) -> None: ...
    def detect_backend(self, peek: bytes, path: Path | None, intent: Intent) -> type[Backend]: ...
    def get_writer_backend(self, format: ArchiveFormat) -> type[Backend]: ...
    def list_formats(self) -> list[ArchiveFormat]: ...  # only available formats
```

### 9.2 Backend ABC

```python
class Backend(ABC):
    FORMAT: ArchiveFormat              # primary format
    FORMATS: tuple[ArchiveFormat, ...]  # all formats this backend handles
    EXTENSIONS: tuple[str, ...]
    MAGIC: bytes
    MAGIC_OFFSET: int = 0
    REQUIRES_SEEK: bool = False        # if True, non-seekable streams are rejected
    SUPPORTS_WRITE: bool = False
    OPTIONAL_DEPENDENCY: str | None = None  # e.g. "py7zr"

    @classmethod
    def detect(cls, peek: bytes) -> bool:
        """Return True if peek bytes match this format's magic."""
        ...

    @abstractmethod
    def open_read(
        self,
        source: Path | BinaryIO,
        intent: Intent,
        password: bytes | None,
        encoding: str,
    ) -> ArchiveReader: ...

    def open_write(
        self,
        dest: Path | BinaryIO,
        compression: CompressionSpec | None,
        password: bytes | None,
        encoding: str,
    ) -> ArchiveWriter:
        raise UnsupportedOperationError(f"{self.FORMAT} write not supported")
```

---

## 10. Per-Format Behavioral Specification

### 10.1 ZIP

| Property | Value |
|----------|-------|
| Backend dependency | `zipfile` (stdlib) |
| Listing cost | O(1) — central directory is read first |
| Access cost | DIRECT — independent local file offsets |
| Supports write | Yes |
| Requires seek | Yes for read (central dir at EOF); No for streaming write |

**Member mapping:**
- `mode`: parsed from `external_attr >> 16`. If `external_attr == 0` and `create_system != 3` (Unix), `mode` is set to `None`.
- `modified`: from `date_time` tuple, constructed as naive `datetime` (no TZ, DOS format has 2-second granularity). If ZIP64 extra field contains an NT timestamp, use that as timezone-aware UTC datetime.
- `type`: inferred from `mode` if Unix, otherwise from `is_dir()` and symlink detection via extra field `0x000A` (NTFS) or `0x7875` (Unix UID/GID).
- `compression`: map `compress_type` integer → `CompressionMethod`.
- `is_encrypted`: `flag_bits & 0x1`.

**Non-seekable ZIP:** Since the central directory lives at EOF, a non-seekable ZIP stream cannot be opened with `Intent.RANDOM`. With `Intent.SEQUENTIAL` or `Intent.AUTO`, the backend buffers to a `tempfile.SpooledTemporaryFile` with a configurable `spool_max_size` (default: 50 MiB) before opening. If the archive exceeds `spool_max_size`, a `ReadError` is raised with a hint to save to disk first.

**Streaming ZIP write:** Uses the `flag_bits |= 0x8` (data descriptor) mode, which allows writing CRC and sizes after the data. File size is not required in advance.

### 10.2 TAR

| Property | Value |
|----------|-------|
| Backend dependency | `tarfile` (stdlib) |
| Listing cost | O(N) — no central directory |
| Access cost | SOLID for `.tar.gz`, `.tar.bz2`, `.tar.xz`, `.tar.zst`; DIRECT for plain `.tar` |
| Supports write | Yes |
| Requires seek | No (streaming mode) |

**Member mapping:**
- `mode`: `TarInfo.mode` (lower 12 bits).
- `modified`: from `TarInfo.mtime` (Unix timestamp, interpret as UTC → timezone-aware).
- PAX extended headers (`pax_headers`) override mtime with full precision and optional TZ info.
- `uname`, `gname`, `uid`, `gid`: direct from `TarInfo`.
- `type`: mapped from TAR type byte (`REGTYPE`, `DIRTYPE`, `SYMTYPE`, `LNKTYPE`, etc.).

**Hardlinks in TAR:** `linkname` field holds the target path. Extraction creates an actual hardlink if the target has already been extracted, or defers to a post-pass if not yet extracted (two-pass extraction). If hardlink creation fails (cross-device), fall back to copying.

**Truncation detection:** After iterating all members, verify that the final 512-byte block(s) are null-filled end-of-archive markers. If not present, emit a `logging.WARNING` and optionally raise `TruncatedError` based on a `strict_eof` parameter (default: warn only).

**TAR variants:** compression is detected from the magic of the first bytes and matched to `tarfile` mode strings (`r:gz`, `r:bz2`, `r:xz`, `r:*` for auto).

### 10.3 Single-file compressors (GZ, BZ2, XZ, ZST)

These are presented as a one-member archive. The single member's name is inferred from the archive filename by stripping the compression extension (e.g. `data.txt.gz` → `data.txt`). If no filename is available, the member name defaults to `"data"`.

| Property | Value |
|----------|-------|
| Listing cost | O(1) — one member always |
| Access cost | SOLID (must decompress from start) |
| Supports write | Yes |
| Requires seek | No |

Member `size` is `None` for GZ (size field is mod-2³² unreliable for >4 GiB); available for BZ2 only after full decompression.

### 10.4 7-Zip (requires `[7z]` extra → `py7zr`)

| Property | Value |
|----------|-------|
| Backend dependency | `py7zr` ≥ 0.20 |
| Listing cost | O(1) — header parsed upfront via `sz.list()` |
| Access cost | SOLID (typically); DIRECT only if no solid blocks |
| Supports write | Yes (via `py7zr`) |
| Requires seek | Yes |

**py7zr push model — no streaming pull.** `py7zr` has no `open(name) -> stream`. The only extraction APIs are `extract(targets=[...], factory=WriterFactory)` and `extractall(factory=WriterFactory)`, which push bytes into `Py7zIO` objects. `reset()` must be called between extractions. The `Py7zIO` objects must be seekable — `py7zr` calls `seek(0)` after each file (as a final rewind; it never reads back), so `BytesIO` and `SpooledTemporaryFile` work; pipes fail.

**Solid archive limitation.** Calling `extract(targets=['c.txt'])` on a solid block `[a, b, c]` still decompresses `a` and `b` — `targets` controls data capture, not decompression work. Therefore calling `_open_member()` naively for each file in a solid block would trigger O(block_files) decompression passes per file = O(N²) total.

**Lazy per-folder caching.** The backend works around this by extracting an entire solid block the first time any member from it is requested, then caching all members:

```
first open(a):  → extract_folder(folder_0)  [decompresses a, b, c]  → cache all three
     open(b):  → cache hit                   [O(1), from SpooledTemporaryFile]
     open(c):  → cache hit                   [O(1)]
first open(d):  → extract_folder(folder_1)  [decompresses d, e]
```

This gives O(1) decompression passes per solid block regardless of access pattern. For sequential `for member in ar: ar.open(member)`, total decompression cost is O(number_of_solid_blocks), not O(N). Memory peak = size of the largest single solid block uncompressed (spilled to disk via `SpooledTemporaryFile` above 64 MiB threshold).

Folder-to-file mapping is available from py7zr internals: each `FileInfo` dict has a `"folder"` key pointing to its `Folder` object; files in the same solid block share the same `Folder` instance. `SubstreamsInfo.num_unpackstreams_folders[i]` gives the file count per folder.

**`_open_member()` implementation:** check `_folder_cache[folder]`; on miss, call `sz.extract(targets=all_files_in_folder, factory=SpooledFactory()); sz.reset()`; on hit, `buf.seek(0); return buf`.

**Solid blocks in CostReceipt:** `solid_block_count` from `archiveinfo().blocks`. `is_solid` from `archiveinfo().solid`.

**Compression chain:** `archiveinfo().method_names` (e.g. `['LZMA2', 'BCJ']`) is archive-level; per-folder codec info is available from `Folder.coders`. Mapped to `CompressionAlgo` values.

**POSIX metadata:** 7z stores POSIX metadata in an optional attribute block. If absent, `mode`, `uid`, `gid` are `None`.

### 10.5 RAR (requires `[rar]` extra → `rarfile` + system `unrar`)

| Property | Value |
|----------|-------|
| Backend dependency | `rarfile` ≥ 4.0 (requires `unrar` binary on PATH) |
| Listing cost | O(1) — central directory parsed upfront |
| Access cost | SOLID if solid archive; DIRECT otherwise |
| Supports write | No — RAR is proprietary; read-only |
| Requires seek | Yes |

**rarfile pull model.** `rarfile.RarFile.open(name)` returns a `RarExtFile` (`RawIOBase`) — a true pull-based stream. For non-solid archives, `rarfile` uses the "hack": it extracts just the target member's compressed bytes into a temp mini-archive and runs `unrar` on that. This is O(member_size) per call.

**Solid archive limitation.** For solid archives, the hack is disabled (`_must_disable_hack()` returns `True`). Every `open()` call runs `unrar` on the full archive from the start. Critically, **`rarfile.extractall()` does not batch-extract** — it calls `open()` once per file internally, spawning a separate `unrar` subprocess for each. Iterating N members of a solid RAR is O(N) subprocess invocations, each processing the full archive — O(N × archive_size) total decompression work.

**Solid RAR workaround — one-shot extraction.** The backend detects solid archives on open and runs the external tool once to extract everything to a `TemporaryDirectory`:

```python
# subprocess.run(['unrar', 'e', '-inul', archive_path, tmpdir + '/'])
#   → one invocation, all files extracted, O(archive_size) total work
```

`_open_member()` for solid archives returns `open(tmpdir / member_relative_path, 'rb')`. The `TemporaryDirectory` is cleaned up in `close()`. Disk space required = uncompressed archive size.

**Non-solid RAR:** uses `rarfile.open()` directly (the hack path, per-file subprocess, O(member_size)).

**No solid block boundary API.** rarfile does not expose which files belong to the same compression block. The hack/no-hack split is binary per archive, not per block.

**RAR4 vs RAR5 timestamp handling:**
- RAR4: stores local wall-clock time → naive `datetime`.
- RAR5: stores UTC with sub-second precision → timezone-aware `datetime`.

**Link handling:** RAR5 stores hardlinks and file-copies via the `file_redir` field. `rarfile` automatically follows `RAR5_XREDIR_HARD_LINK` and `RAR5_XREDIR_FILE_COPY` redirects inside `open()`, transparently returning the source file's data. Symlinks (`RAR5_XREDIR_UNIX_SYMLINK`) are stored with the link target path as the content; the ABC-level link-following described in §3.2 handles these uniformly across formats.

**Header encryption (RAR5):** `ArchiveInfo.is_encrypted = True`; listing requires password.

### 10.6 ISO 9660 (requires `[iso]` extra → `pycdlib`)

| Property | Value |
|----------|-------|
| Backend dependency | `pycdlib` |
| Listing cost | O(1) — directory tree in header region |
| Access cost | DIRECT |
| Supports write | No (pycdlib supports write but out of scope) |
| Requires seek | Yes |

**Namespace selection:** The backend auto-selects the richest available namespace in priority order: Rock Ridge → Joliet → Plain ISO 9660. The selected namespace is reported in `ArchiveInfo.extra["iso.namespace"]`. Rock Ridge preserves full POSIX metadata and long filenames; plain ISO 9660 truncates filenames to 8.3 and loses case.

### 10.7 Directory

A plain filesystem directory is treated as a zero-cost pseudo-archive: `ListingCost.O1`, `AccessCost.DIRECT`, fully seekable. Useful for conversion pipelines where the "source" is an existing directory.

---

## 11. Writing and Conversion

### 11.1 CompressionSpec

```python
@dataclass
class CompressionSpec:
    algo: CompressionAlgo = CompressionAlgo.DEFLATE
    level: int | None = None   # None = library default

# Convenience constants:
CompressionSpec.STORED    = CompressionSpec(algo=CompressionAlgo.STORED)
CompressionSpec.DEFLATE   = CompressionSpec(algo=CompressionAlgo.DEFLATE, level=6)
CompressionSpec.DEFLATE_MAX = CompressionSpec(algo=CompressionAlgo.DEFLATE, level=9)
CompressionSpec.LZMA      = CompressionSpec(algo=CompressionAlgo.LZMA2, level=6)
```

### 11.2 Conversion semantics

`writer.add_members(reader)` must:
1. Iterate `reader` sequentially (respecting `AccessCost.SOLID` naturally).
2. For each member, call `reader.open(member)` and stream the result into `writer.add_stream(...)`.
3. Translate the `Member` metadata (name, mode, timestamps) directly — no re-encoding.
4. Skip members with types unsupported by the target format, emitting `logging.WARNING`.
5. Not buffer the entire member data in memory; use a configurable chunk size (default: 1 MiB).

---

## 12. Progress Reporting

```python
@dataclass
class ExtractionProgress:
    member: Member
    bytes_written: int
    total_bytes_estimated: int | None   # None if archive has no size info
    members_done: int
    members_total: int | None

@dataclass
class ExtractionResult:
    member: Member
    path: Path | None           # None if skipped
    status: ExtractionStatus    # EXTRACTED, SKIPPED, REJECTED

class ExtractionStatus(Enum):
    EXTRACTED = "extracted"
    SKIPPED   = "skipped"       # due to OverwritePolicy.SKIP
    REJECTED  = "rejected"      # due to filter rejection; no exception raised if
                                # on_rejection=OnRejection.WARN (default: RAISE)
```

---

## 13. Logging

All logging uses `logging.getLogger("archivey")` and its children:
- `archivey.detection` — format detection events
- `archivey.normalization` — path normalization changes
- `archivey.extraction` — extraction events and filter decisions
- `archivey.backends.*` — backend-specific debug messages

The library never configures handlers or levels — that is left entirely to the application.

---

## 14. Testing Contract

### 14.1 Equivalence matrix

The test suite must demonstrate that extracting a canonical directory structure (files, symlinks, nested dirs, empty dirs, filenames with unicode and spaces) produces **identical** `Member` objects from ZIP, TAR, 7z, RAR, and ISO sources (modulo documented format limitations). Equivalence is defined as field-by-field equality excluding `sequence`, `original_name`, `compressed_size`, and `extra`.

### 14.2 Adversarial corpus

The adversarial test corpus must include:
- **Zip bomb:** quine-style and nested (42.zip variant) — verify `max_ratio` and `max_extracted_bytes` limits.
- **Path traversal:** `../evil`, `../../etc/passwd`, `./../../outside` — verify `PathTraversalError`.
- **Absolute paths:** `/etc/passwd`, `C:\Windows\System32\evil.dll` — verify `PathTraversalError`.
- **Symlink escape:** symlink pointing to `../../outside`, and chained symlinks — verify `SymlinkEscapeError`.
- **Corrupt archive:** truncated ZIP (missing EOCD), truncated TAR, bad CRC — verify `CorruptionError` / `TruncatedError`.
- **Unicode bombs:** `\x00` in paths, RTL override characters in filenames.
- **Giant uncompressed size in header:** member claims 1 TiB size but archive is 1 KiB — verify extraction aborts cleanly.

### 14.3 Round-trip test

For every writable format: `create → extract → compare` must produce identical files and metadata (within format-documented timestamp/permission limitations).

### 14.4 Non-seekable stream test

Every backend that supports streaming must be tested with a `FakeNonSeekable` wrapper that raises `io.UnsupportedOperation` on all seek/tell calls.

---

## 15. Sample Usage Patterns

This section establishes the canonical patterns for common tasks. Test suites must cover each of these patterns across all supported formats.

### 15.1 Basic iteration and extraction

```python
import archivey

# One-shot safe extraction (most common case)
archivey.extract("untrusted.zip", "/safe/output/")

# Inspect members before deciding to extract
with archivey.open("archive.tar.gz") as ar:
    print(ar.info)                      # format, solid, cost receipt
    for member in ar:
        print(member.name, member.size, member.type)
```

### 15.2 Computing file hashes without writing to disk

The standard iterator with `open()` is all you need. The library handles solid archive efficiency internally.

```python
import archivey
import hashlib

with archivey.open("archive.7z") as ar:
    for member in ar:
        if member.type != archivey.MemberType.FILE:
            continue
        h = hashlib.sha256()
        with ar.open(member) as f:
            while chunk := f.read(65536):
                h.update(chunk)
        print(f"{h.hexdigest()}  {member.name}")
```

This pattern works correctly and efficiently across all formats. For solid 7z archives, the backend decompresses each solid block exactly once — subsequent `open()` calls for files in the same block are served from an internal cache. For solid RAR archives, the backend runs `unrar` once on first access and caches to disk. The caller writes no code to handle these cases.

### 15.3 Opening a symlink or hardlink

`open()` and `read()` transparently follow links that point to other members in the same archive, regardless of format. This mirrors `tarfile.extractfile()` behavior.

```python
with archivey.open("archive.tar") as ar:
    # Suppose the archive contains:
    #   data/v1.0/report.txt  (regular file)
    #   data/latest           (symlink -> v1.0/report.txt)
    #   data/also-latest      (hardlink -> data/v1.0/report.txt)

    # All three produce the same bytes:
    content_a = ar.read("data/v1.0/report.txt")
    content_b = ar.read("data/latest")        # follows symlink
    content_c = ar.read("data/also-latest")   # follows hardlink
    assert content_a == content_b == content_c

    # The Member object itself still reflects the link type:
    link_member = ar["data/latest"]
    assert link_member.type == archivey.MemberType.SYMLINK
    assert link_member.link_target == "v1.0/report.txt"
```

If a link's target is not present in the archive (e.g. an external symlink), `open()` raises `ReadError`.

### 15.4 Format conversion (streaming, no intermediate file)

```python
import archivey

# Convert tar.gz to zip — streams member data directly, no buffering of full archive
with archivey.open("input.tar.gz") as reader, \
     archivey.create("output.zip") as writer:
    writer.add_members(reader)

# You can filter during conversion:
with archivey.open("input.tar.gz") as reader, \
     archivey.create("output.zip") as writer:
    for member, stream in reader.iter_with_data():
        if member.name.endswith(".py"):
            writer.add_stream(stream, name=member.name, modified=member.modified)
```

### 15.5 Checking the cost receipt before committing

```python
import archivey
from archivey import AccessCost

with archivey.open("mystery.7z") as ar:
    if ar.cost.access_cost == AccessCost.SOLID:
        # Solid: sequential iteration is efficient (backend caches per block).
        # Random access still works but triggers per-block decompression on cache miss.
        print(f"Solid archive: {ar.cost.solid_block_count} block(s). Iterating sequentially.")
        for member in ar:
            process(member, ar.open(member))
    else:
        # Direct access — jump to any member cheaply
        for name in interesting_names:
            data = ar.read(name)
            process_data(name, data)
```

### 15.6 Creating an archive from a stream source

```python
import archivey, io

with archivey.create("report.zip") as writer:
    # From bytes
    writer.add_bytes(b"Hello, world!", name="greeting.txt")

    # From a BinaryIO stream (size known in advance)
    with open("large_data.bin", "rb") as f:
        writer.add_stream(f, name="data/large.bin", size=os.path.getsize("large_data.bin"))

    # From the filesystem (recursively)
    writer.add("src/", name="source/")
```

---

## Appendix A: Deferred / Out-of-Scope Items

These were considered and deliberately excluded from v1:

- **In-place modification:** Archive append/update is architecturally incompatible with ZIP and 7z and adds significant complexity. Omitted.
- **Encryption write for 7z/RAR:** RAR write is proprietary; 7z encryption via `py7zr` may be added as `[7z]` extra feature.
- **Native sparse file extraction:** Sparse file support in TAR is detected and flagged but extracted as dense files to avoid cross-platform filesystem complexity.
- **NTFS junction recreation on non-Windows:** Junction points are presented as `MemberType.SYMLINK` with `extra["zip.is_junction"] = True` but recreated only on Windows.
- **Multi-volume archives:** `ArchiveInfo.is_multivolume = True` is reported, but joining volumes is left to the caller.
- **Async API:** A purely synchronous API is specified for v1. An async wrapper layer is a natural follow-on.
