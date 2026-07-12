> **Grab-bag / historical.** Not normative — see `openspec/specs/`. Index: [grab-bag/index.md](index.md).

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
| Optional extras | `[7z]`, `[rar]`, `[crypto]`, `[7z-write]`, `[iso]`, `[zstd]`, `[lz4]`, `[cli]`, `[seekable]`, `[recommended-lite]`, `[recommended]`, `[all]` (RAR member-data decompression additionally needs the system `unrar` binary) |
| OS support | Linux, macOS, Windows |
| Thread safety | The `ArchiveReader` object is not thread-safe. `open_archive(..., member_streams=MemberStreams.CONCURRENT)` unlocks concurrent `open()` after materialization; see `MemberStreams`. Writers are not thread-safe. |

---

## 3. Public API Surface

### 3.1 Top-level functions

```python
# Open an archive for reading
archivey.open_archive(
    source: str | Path | BinaryIO | Sequence[str | Path | BinaryIO],
    *,
    format: ArchiveFormat | None = None,  # override detection
    streaming: bool = False,              # False = random access; True = forward-only, one pass
    password: str | bytes | Sequence[str | bytes] | PasswordProvider | None = None,
    encoding: str | None = None,         # None = auto-detect member-name encoding
    config: ArchiveyConfig | None = None,  # tuning knobs; None = DEFAULT_ARCHIVEY_CONFIG
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

# One-shot extraction (most common use case) — extracts ALL members.
# There is deliberately no `members=` selector: passing pre-fetched members to a
# one-shot function would force the caller to open the archive, fetch the list, and
# reopen it here (an anti-pattern). Selective extraction is done on an already-open
# reader via `reader.extract_all(members=..., filter=...)`.
archivey.extract(
    source: str | Path | BinaryIO | Sequence[str | Path | BinaryIO],
    dest: str | Path,
    *,
    policy: ExtractionPolicy = ExtractionPolicy.STRICT,
    overwrite: OverwritePolicy = OverwritePolicy.ERROR,   # pre-existing files
    on_error: OnError = OnError.STOP,                     # member extraction failures
    format: ArchiveFormat | None = None,
    password: str | bytes | Sequence[str | bytes] | PasswordProvider | None = None,
    encoding: str | None = None,         # member-name encoding for TAR/ZIP
    on_progress: Callable[[ExtractionProgress], None] | None = None,
    config: ArchiveyConfig | None = None,     # tuning knobs; None = DEFAULT_ARCHIVEY_CONFIG
    limits: ExtractionLimits | None = None,   # per-call bomb-limit override; None = config's
) -> list[ExtractionResult]

# Detect format without opening
archivey.detect_format(
    source: str | Path | BinaryIO,
) -> FormatInfo
```

`source` for `open_archive()` may also be an ordered `Sequence[...]` of files/streams
that together form a single multi-volume archive (the library joins them in order).

### 3.2 ArchiveReader

```python
# Shared selector/filter vocabulary (also used by extract_all):
MemberSelector = Collection[ArchiveMember | str] | Callable[[ArchiveMember], bool]
MemberFilter   = Callable[[ArchiveMember], ArchiveMember | None]  # return a (possibly
                 #   .replace()'d) member, or None to skip

class ArchiveReader:
    # --- Metadata ---
    @property
    def info(self) -> ArchiveInfo: ...
    @property
    def cost(self) -> CostReceipt: ...
    @property
    def format(self) -> ArchiveFormat: ...

    # --- Member iteration ---
    def __iter__(self) -> Iterator[ArchiveMember]: ...    # sequential, in-order
    def members(self) -> list[ArchiveMember]: ...         # materializes all (may trigger scan)
    # (deliberately no __len__/__getitem__: the reader is not a collection; see the
    # archive-reading spec. `member in reader` is identity membership, any mode.)
    def __contains__(self, member: ArchiveMember) -> bool: ...

    # --- Name lookup (default streaming=False; raises under streaming=True) ---
    def get(self, name: str, default=None) -> ArchiveMember | None: ...

    # --- Data access ---
    def read(self, member: str | ArchiveMember) -> bytes: ...   # WARNING: loads the whole
                                                                # decompressed payload into RAM
    def open(self, member: str | ArchiveMember) -> BinaryIO: ...   # streaming; caller must close

    # --- Sequential streaming (bounded memory) ---
    # Yields (member, stream) pairs in archive order with bounded memory.
    # `members` selects which members to yield (collection of members/names, or a
    # predicate); None = all. There is intentionally NO transform `filter` here:
    # stream_members yields the ORIGINAL mutable member so the backend can keep
    # filling late-bound fields (final size/CRC, data-stored link_target) in place.
    # Transformation lives at the sinks (extract_all / writer.add_members). Streams
    # are opened lazily, so unselected members cost nothing.
    # The stream is only valid until the iterator advances; do not hold it across yields.
    # For non-file members the stream is None.
    def stream_members(
        self,
        members: MemberSelector | None = None,
    ) -> Iterator[tuple[ArchiveMember, BinaryIO | None]]: ...

    # --- Extraction helper (delegates to archivey.extract internals) ---
    # There is no single-member extract(); extracting one file is extract_all(dest,
    # members=[name]), which is also strictly better on solid archives (a selected set
    # costs one pass, vs. re-decompressing per file).
    def extract_all(
        self,
        dest: str | Path,
        *,
        members: MemberSelector | None = None,  # names/members or predicate; None = all
        filter: MemberFilter | None = None,      # per-member sanitize/rename; None to skip
        policy: ExtractionPolicy = ExtractionPolicy.STRICT,
        overwrite: OverwritePolicy = OverwritePolicy.ERROR,
        on_error: OnError = OnError.STOP,
        on_progress: Callable[[ExtractionProgress], None] | None = None,
        config: ArchiveyConfig | None = None,     # overrides the reader's config for this call
        limits: ExtractionLimits | None = None,   # per-call bomb-limit override
    ) -> list[ExtractionResult]: ...

    # --- Context manager ---
    def __enter__(self) -> ArchiveReader: ...
    def __exit__(self, *_) -> None: ...
    def close(self) -> None: ...
```

**Constraint:** calling `get`, or random `extract_all(members=[name])`, on a reader opened with `streaming=True` raises `UnsupportedOperationError`. `members()` likewise raises under `streaming=True`, since it requires materializing all members. The enforcement is **uniform** — it does not depend on whether a backend happens to have an index loaded — so streaming behaviour is deterministic across formats. (`get_members_if_available()` is exempt: it never scans, so it stays callable.)

**Two sequential access patterns — different memory profiles:**

| Pattern | Memory profile | When to use |
|---------|---------------|-------------|
| `for m, f in ar.stream_members()` | Bounded and small — decompression is always streaming; a solid block is decompressed progressively as its members are consumed. Peak ≈ decompressor working state + one in-flight chunk, **not** a whole block. | Sequential one-pass processing: hashing, conversion, scanning. |
| `for m in ar: ar.open(m)` | Bounded, but re-does work on solid blocks — `open()` on a member inside a solid block re-decompresses that block from its start and skips to the member, emitting a `logging.WARNING`. **No growing decompressed cache is ever held** until `close()`. | Random or mixed access on `DIRECT` formats; acceptable on solid formats only for a few members. |

Decompression is **always streaming**; the library MUST NOT hold a monotonically-growing cache of decompressed block data released only at `close()`. On a solid archive, repeated `open()` calls trade CPU (re-decompression from the block start) for bounded memory, and emit a `logging.WARNING` via `archivey.backends` advising `stream_members()` for full sequential passes. For formats without solid compression (ZIP, plain `.tar`, single-file `.gz`), both patterns are equally efficient — `open()` seeks directly to the member with no re-decompression.

**`read()` is the unbounded option — use it only for small members.** Unlike `open()` and `stream_members()`, `read(member)` materializes the member's **entire decompressed payload in memory at once** and returns it as `bytes`. It is for small, known-bounded members (config files, manifests, small assets). For large or untrusted members, use `open()` (chunked streaming) or `stream_members()` (bounded sequential) — neither buffers the whole payload — and note `read()` performs no decompression-bomb check, so a hostile member can expand without limit.

**Link following:** `open()` and `read()` transparently follow symlinks and hardlinks that point to other members in the same archive. If `member.type` is `SYMLINK` or `HARDLINK`, the call is redirected to the target member (also exposed, when known, as `member.link_target_member`). **Hardlinks always resolve to an *earlier* member** (the TAR model — a hardlink entry refers back to a previously-seen file), so they can be resolved during a single forward pass. If the link target is not present in the archive, `LinkTargetNotFoundError` is raised. Chains are followed recursively with **cycle detection**: the set of members already visited on the current chain is tracked, and revisiting one raises a `ReadError` reporting the cycle. There is **no fixed depth limit** — an acyclic chain of any length resolves, and only a genuine cycle (or a missing target) fails. This behavior is format-independent and implemented once in the `ArchiveReader` ABC — it does not rely on format-level link resolution (which happens at a lower level for formats that follow links internally).

### 3.3 ArchiveWriter

```python
class ArchiveWriter:
    def add_file(
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

    def add_member(self, member: ArchiveMember, data: BinaryIO) -> None: ...

    def add_members(
        self,
        source: ArchiveReader | Iterable[tuple[ArchiveMember, BinaryIO | None]],
        *,
        filter: MemberFilter | None = None,   # transform/rename/skip, applied writer-side
    ) -> None: ...

    def __enter__(self) -> ArchiveWriter: ...
    def __exit__(self, *_) -> None: ...
    def close(self) -> None: ...
```

`add_members` is the streaming conversion primitive: it streams data directly from the
source to the writer without buffering the whole archive. It accepts **either** an
`ArchiveReader` (whole-archive conversion — it drives `reader.stream_members()`
internally) **or** an iterable of `(member, stream)` pairs, which is exactly the shape
`ArchiveReader.stream_members()` yields. **Selection** stays on the reader
(`stream_members(members=...)`, which yields the *original* members); **transformation**
(`filter`) is applied here on the writer side, to a transient `.replace()` copy used for
the written entry's identity while the original streams through — so a renaming `filter`
never detaches the member from the backend's in-place late-bound updates. Thus
`writer.add_members(reader.stream_members(predicate), filter=rename)` converts a selected,
renamed subset in a single streaming pass **without reopening** the source. (The transform
filter lives here rather than on `stream_members()` for the late-bound reason noted in §3.2.)
The writer may buffer internally per its format requirements (e.g. ZIP local headers
need the CRC before writing), but only on a per-member basis — never the full archive.

---

## 4. Data Model

### 4.1 ArchiveFormat

A format is modeled as the **composition** of a container (member layout) and an outer
single-stream codec, not a flat enum. The familiar named formats are predefined
`(container, stream)` class-vars, so callers keep writing `ArchiveFormat.TAR_GZ` while the
model underneath lets `tar × {gzip, bzip2, xz, zstd, lz4, …}` be expressed without a
combinatorial enum.

```python
class ContainerFormat(StrEnum):
    ZIP        = "zip"
    TAR        = "tar"
    RAR        = "rar"
    SEVEN_Z    = "7z"
    ISO        = "iso"
    DIRECTORY  = "directory"   # plain filesystem directory
    RAW_STREAM = "raw_stream"  # a bare single-file compressed stream (no container)
    UNKNOWN    = "unknown"

class StreamFormat(StrEnum):
    UNCOMPRESSED = "uncompressed"
    GZIP         = "gz"
    BZIP2        = "bz2"
    XZ           = "xz"
    ZSTD         = "zst"     # requires [zstd] extra
    LZ4          = "lz4"     # requires [lz4] extra
    # extensible: new outer codecs are added here (lzip, brotli, …)

@dataclass(frozen=True)
class ArchiveFormat:
    container: ContainerFormat
    stream: StreamFormat

    def file_extension(self) -> str: ...   # e.g. ("tar","gz") -> "tar.gz"

    # Predefined named instances (class vars):
    ZIP       = ArchiveFormat(ContainerFormat.ZIP,        StreamFormat.UNCOMPRESSED)
    TAR       = ArchiveFormat(ContainerFormat.TAR,        StreamFormat.UNCOMPRESSED)
    TAR_GZ    = ArchiveFormat(ContainerFormat.TAR,        StreamFormat.GZIP)
    TAR_BZ2   = ArchiveFormat(ContainerFormat.TAR,        StreamFormat.BZIP2)
    TAR_XZ    = ArchiveFormat(ContainerFormat.TAR,        StreamFormat.XZ)
    TAR_ZST   = ArchiveFormat(ContainerFormat.TAR,        StreamFormat.ZSTD)   # [zstd]
    TAR_LZ4   = ArchiveFormat(ContainerFormat.TAR,        StreamFormat.LZ4)    # [lz4]
    GZ        = ArchiveFormat(ContainerFormat.RAW_STREAM, StreamFormat.GZIP)
    BZ2       = ArchiveFormat(ContainerFormat.RAW_STREAM, StreamFormat.BZIP2)
    XZ        = ArchiveFormat(ContainerFormat.RAW_STREAM, StreamFormat.XZ)
    ZST       = ArchiveFormat(ContainerFormat.RAW_STREAM, StreamFormat.ZSTD)   # [zstd]
    SEVEN_Z   = ArchiveFormat(ContainerFormat.SEVEN_Z,    StreamFormat.UNCOMPRESSED)  # read native; write [7z-write]
    RAR       = ArchiveFormat(ContainerFormat.RAR,        StreamFormat.UNCOMPRESSED)  # native metadata + system `unrar`
    ISO       = ArchiveFormat(ContainerFormat.ISO,        StreamFormat.UNCOMPRESSED)  # [iso]
    DIRECTORY = ArchiveFormat(ContainerFormat.DIRECTORY,  StreamFormat.UNCOMPRESSED)
    UNKNOWN   = ArchiveFormat(ContainerFormat.UNKNOWN,    StreamFormat.UNCOMPRESSED)
```

Because `ArchiveFormat` is `frozen=True` it stays hashable and usable as a dict key.
Equality is **compositional**: the predefined class-vars compare equal to any
structurally equal pair, so
`ArchiveFormat.TAR_GZ == ArchiveFormat(ContainerFormat.TAR, StreamFormat.GZIP)`.

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
    BROTLI   = "brotli"
    PPMD     = "ppmd"
    BCJ      = "bcj"           # x86 executable filter
    BCJ2     = "bcj2"
    DELTA    = "delta"
    UNKNOWN  = "unknown"       # unrecognized codec ID
    # This enum is extensible: new codecs are appended as formats are supported. A
    # codec Archivey does not recognize maps to UNKNOWN (never an exception), so
    # callers SHOULD treat the set as open-ended.

@dataclass(frozen=True)
class CompressionMethod:
    algo: CompressionAlgo
    level: int | None = None        # compression level if known
    properties: bytes | None = None # raw codec properties blob
```

A `tuple[CompressionMethod, ...]` models a filter chain (e.g., `(CompressionMethod(BCJ2), CompressionMethod(LZMA2))` for a typical 7z executable entry).

### 4.4 ArchiveMember

`ArchiveMember` is **mutable** (not frozen). Several fields are genuinely unknown when a
member is first yielded and only become known once its data has been read — the final
`size`/CRC of a gzip stream or a ZIP data-descriptor entry, or a `link_target` stored in
(or encrypted within) the member's *data* rather than its header. The library fills these
fields **in place** as it streams, so the `ArchiveMember` a caller already holds gains its
late values without a re-fetch (required under `streaming=True`, where the member list
cannot be materialized and re-read).

Because the object is mutable, the contract is: **callers MUST treat an `ArchiveMember`
as read-only** — the library is the only writer. A caller or filter that needs an altered
member calls `.replace(**kwargs)`, which returns a **copy** and never mutates the
original. As a consequence, `ArchiveMember` is **unhashable** (a mutable value object must
not be a dict key or set element); callers key by `member.name` or `member.member_id`.

```python
@dataclass
class ArchiveMember:
    # --- Type ---
    type: MemberType

    # --- Identity ---
    name: str                               # normalized: forward slashes; trailing / for dirs; no leading /
    raw_name: bytes | None                  # verbatim name bytes as stored, before decode/normalize;
                                            #   None if the format stores no separate raw form. Kept as
                                            #   bytes so a wrong encoding guess can never garble it and
                                            #   the name can be re-decoded losslessly.

    # --- Sizes (None if format cannot provide) ---
    size: int | None                        # uncompressed size in bytes (may be filled after reading)
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
    link_target: str | None                 # SYMLINK/HARDLINK target path as stored (not normalized);
                                            #   may be None until filled while streaming
    link_target_member: "ArchiveMember | None"  # the resolved target member within this same archive,
                                            #   or None when unknown, not yet resolved (streaming),
                                            #   or absent from the archive

    # --- Compression ---
    compression: tuple[CompressionMethod, ...] = ()

    # --- Flags ---
    is_encrypted: bool = False
    is_sparse: bool = False                 # TAR sparse files; extraction as regular file

    # --- Integrity ---
    # Per-algorithm digests, keyed by lowercase algorithm name. CRC32 values are ints
    # ("crc32"); cryptographic/other digests are raw bytes ("blake2sp", "sha256", ...).
    # Empty when the format records no integrity data. A format MUST NOT report one
    # algorithm's value under another's key. Excluded from __eq__.
    hashes: Mapping[str, int | bytes] = field(default_factory=dict, compare=False)

    # --- Format-specific overflow ---
    # Keys are namespaced: "zip.extra_fields", "tar.pax_headers", "iso.rock_ridge", etc.
    # Excluded from __eq__: format-specific extras don't affect logical identity.
    extra: dict[str, Any] = field(default_factory=dict, compare=False)

    # Provenance/metadata fields (None when unrecorded): comment, create_system
    #   (a CreateSystem enum mirroring ZIP's FAT/UNIX/NTFS/… values), windows_attrs.

    @property
    def member_id(self) -> int: ...         # stable 0-based position; assigned at registration
    @property
    def archive_id(self) -> str: ...        # owning-archive id; validates a member passed back in

    # Read-only convenience helpers (never settable):
    @property
    def is_file(self) -> bool: ...          # also is_dir / is_link / is_other / is_junction

    def replace(self, **kwargs: Any) -> "ArchiveMember":
        """Return a *copy* with the given fields changed; never mutates self.
        Filters use this to sanitize/rename a member without touching the original."""
```

There is **no `crc32` field** — integrity lives in `hashes` (e.g. `member.hashes.get("crc32")`);
`hashes` and `extra` are excluded from `__eq__`. The object is **not hashable**. The
`is_*` helpers plus `comment`/`create_system`/`windows_attrs` are carried for ergonomics;
Archivey deliberately omits `zipfile`-compat aliases (`date_time`, `CRC`, naive `mtime`).

**Normalization rules for `name`** (applied in order):
1. Replace all `\` with `/`.
2. Strip leading `/` and `./`.
3. Collapse `//` and `foo/../bar` sequences.
4. Append `/` for directories if not present.
5. Never produce an empty string — root dir becomes `"."`.

`name` is produced by decoding the stored bytes (using the format's internal encoding
signal where present, otherwise the resolved/auto-detected `encoding`) and then applying
the rules above. `raw_name` holds the **verbatim bytes as stored**, before any decode or
normalization, so the name can be re-decoded losslessly under a different encoding; it is
`None` only when the format exposes no separate raw form. When normalization changes the
logical path, a warning is emitted via the `archivey.normalization` logger.

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

`is_solid` is the canonical solidity flag: the archive is **solid** when decompressing one
member may require decompressing other members before it (members share a compression
stream / solid block). It lives here on `ArchiveInfo`; the embedded `CostReceipt` does
**not** repeat it (it carries `access_cost` and `solid_block_count` instead).

### 4.6 CostReceipt

The receipt describes three **independent** axes plus a solid-block count.

```python
class ListingCost(Enum):
    """How expensive it is to ENUMERATE all members (list names + metadata)."""
    INDEXED                = "indexed"                # an index / central directory is present;
                                                      #   listing is O(1) regardless of archive size
    REQUIRES_SCANNING      = "requires_scanning"      # no index, but members can be enumerated by
                                                      #   seeking/scanning header-to-header without
                                                      #   decompressing payload (e.g. uncompressed tar,
                                                      #   or a RAR with no quick-open record)
    REQUIRES_DECOMPRESSION = "requires_decompression" # the stream must be decompressed to reach the
                                                      #   member headers (e.g. a compressed tar)

class AccessCost(Enum):
    """How expensive it is to READ one member's data, given the FORMAT layout."""
    DIRECT = "direct"   # any member can be read without touching other members
    SOLID  = "solid"    # reading member N may require decompressing earlier members in its block

class StreamCapability(Enum):
    """A property of the underlying SOURCE bytes, independent of the format layout."""
    SEEKABLE     = "seekable"      # the source supports arbitrary seek(); positions can be revisited
    FORWARD_ONLY = "forward_only"  # non-seekable source (pipe/socket): it cannot be rewound at all.
                                   #   Re-reading any earlier position requires a brand-new stream.

@dataclass(frozen=True)
class CostReceipt:
    listing_cost: ListingCost
    access_cost: AccessCost
    stream_capability: StreamCapability
    solid_block_count: int | None   # number of distinct solid blocks (each one decompress pass),
                                    #   or None when not applicable / unknown. is_solid lives on
                                    #   ArchiveInfo, not here, to avoid duplicating the flag.
    notes: tuple[str, ...] = ()     # human-readable caveats
```

**The three axes are orthogonal and MUST NOT be conflated:**

- `stream_capability` is about the **source byte stream** — can the raw bytes be
  `seek()`ed? A file on disk is `SEEKABLE`; a socket or pipe is `FORWARD_ONLY`. A
  `FORWARD_ONLY` source cannot be rewound at all — not even to re-read an earlier member.
- `access_cost` is about the **format layout** — is member N's data independent
  (`DIRECT`) or entangled with earlier members in a shared compression stream (`SOLID`)?
  "Rewinding a decompressed stream costs a re-decompress from the block start" belongs
  here — it is a consequence of `SOLID` (and `ArchiveInfo.is_solid`), *not* of source
  seekability.
- `listing_cost` is about **enumeration** — getting names+metadata for all members.

They compose. Examples:

- **ZIP** on a file: `INDEXED` + `DIRECT` + `SEEKABLE`.
- **plain `.tar`** on a file: `REQUIRES_SCANNING` + `DIRECT` + `SEEKABLE`.
- **plain `.tar`** on a pipe: `REQUIRES_SCANNING` + `DIRECT` + `FORWARD_ONLY`.
- **`.tar.gz`** on a file: `REQUIRES_DECOMPRESSION` + `SOLID` + `SEEKABLE` (the *source*
  seeks, even though random member access still costs a re-decompress).
- **7z** solid: `INDEXED` + `SOLID` + `SEEKABLE`, with `solid_block_count` = number of
  solid folders.

### 4.7 FormatInfo (detection result)

```python
class DetectionConfidence(Enum):
    CERTAIN  = "certain"    # exact magic-byte match at the expected offset
    PROBABLE = "probable"   # structural/content probe (inner-tar probe, SFX signature scan)
    GUESS    = "guess"      # file extension only, no content confirmation

@dataclass(frozen=True)
class FormatInfo:
    format: ArchiveFormat
    confidence: DetectionConfidence
    detected_by: str                # "magic", "extension", "content_probe", "sfx_scan"
    encoding_hint: str | None       # suggested encoding for member-name fields, from
                                    #   FORMAT-LEVEL signals only (UTF-8 bit, code-page,
                                    #   BOM) — never a member scan; None if no signal
    payload_offset: int = 0         # byte offset of the archive payload; nonzero for SFX
                                    #   archives behind an executable stub (is-SFX == > 0)
```

`confidence` is an enum, not a float, because detection has a few discrete outcomes
(exact magic, structural probe, extension guess), not a continuous score. `encoding_hint`
is derived only from **format-level signals** detection can see cheaply; it is `None` when
the format exposes no such signal, in which case `open_archive()` falls back to its own
auto-detection/`encoding` handling. `payload_offset > 0` is the SFX indicator; there is no
separate boolean.

---

## 5. Enums and Policies

### 5.1 Access mode (`streaming`)

Access mode is a single boolean on `open_archive()` (`streaming: bool = False`), not an
enum. There are exactly two modes:

- `streaming=False` (**default**) — **random access**. The library loads index structures
  (central directories, 7z headers) when available and presents the archive for arbitrary
  member access. It **requires a source it can random-access** and fails fast at
  `open_archive()` if the source is non-seekable and the format cannot adapt — it does
  **not** silently degrade to forward-only (which would surface failures only later, at
  read time). For seekable single-stream formats, seek points (the index that makes random
  access into a compressed stream affordable) are built **lazily** — only if the caller
  actually `seek()`s.
- `streaming=True` — **forward-only, single pass**. The caller promises one forward pass;
  index loading is disabled where possible, and any source works (including non-seekable
  pipes/sockets). All random-access and full-materialization methods raise
  `UnsupportedOperationError`, uniformly across formats.

Eager seek-point building (the old `Intent.RANDOM` promise from an earlier draft) is
intentionally **not** exposed; it can return later as an explicit opt-in flag if a need
arises. See the `access-mode-and-cost` capability spec for the full method-by-mode table.

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

**Relationship to Python's `tarfile` filters.** These policies parallel `tarfile`'s named
filters (`data`, `tar`, `fully_trusted`) so callers can transfer that mental model, but the
names differ deliberately because Archivey applies them uniformly across **all** formats,
not just TAR, and the per-bit transforms are Archivey's own:

| `ExtractionPolicy` | Closest `tarfile` filter | Notable differences |
|---|---|---|
| `STRICT` (default) | `data` | Like `data` (blocks unsafe paths/links/special files), and additionally strips execute bits and normalizes permissions to 644/755. Archivey's default; `tarfile`'s default varies by Python version. |
| `STANDARD` | `tar` | Like `tar` (strips setuid/setgid/sticky and group/other-write intent), but Archivey still drops uid/gid and keeps the universal path-safety checks that `tarfile`'s `tar` filter does not all guarantee. |
| `TRUSTED` | `fully_trusted` | Applies stored mode and (as root) uid/gid. Unlike `fully_trusted`, Archivey **still enforces** the non-bypassable universal path/symlink/special-file constraints. |

### 5.3 OverwritePolicy and OnError

`OverwritePolicy` governs **pre-existing destination files**; `OnError` governs
**member extraction failures** (corrupt/encrypted data, ratio bomb, write error, or a
safety-filter rejection). They are independent knobs.

```python
class OverwritePolicy(Enum):
    ERROR   = "error"   # raise ExtractionError if destination file exists
    SKIP    = "skip"    # silently skip existing files
    REPLACE = "replace" # overwrite unconditionally

class OnError(Enum):
    STOP     = "stop"      # default: raise the first failure and halt (no further members)
    CONTINUE = "continue"  # best-effort: clean up the partial file, record FAILED/REJECTED
                           #   (with the error) in the result list, and proceed
```

A per-member failure is usually an `ArchiveyError`, but it **also includes a plain
filesystem `OSError`** raised while reading the member's bytes out of the source or writing
its output file (hence `ExtractionResult.error: ArchiveyError | OSError`); such `OSError`s
are caught/recorded under `CONTINUE`, not propagated. Under `OnError.CONTINUE` the returned
`list[ExtractionResult]` is the report (no aggregate exception is raised); the cumulative
`max_extracted_bytes` bomb limit and `KeyboardInterrupt`/`MemoryError` (and any other
non-`ArchiveyError`/non-`OSError` exception) always halt regardless. See §7 /
`safe-extraction`.

---

## 6. Exception Hierarchy

```
ArchiveyError(Exception)
├── OpenError                   # cannot open / parse the archive header
│   ├── FormatDetectionError    # could not detect format
│   ├── UnsupportedFormatError  # format detected but no backend available
│   └── StreamNotSeekableError  # source is non-seekable but this format/backend needs seek
├── ReadError                   # error reading a member
│   ├── CorruptionError         # CRC mismatch, bad data block
│   ├── TruncatedError          # unexpected EOF
│   ├── EncryptionError         # password required or wrong password
│   └── LinkTargetNotFoundError # a symlink/hardlink target is absent from the archive
├── WriteError                  # error writing an archive
├── ExtractionError             # error extracting a member to disk
│   └── FilterRejectionError    # safety filter blocked the member
│       ├── PathTraversalError  # ../ or absolute path
│       ├── SymlinkEscapeError  # symlink resolves outside dest
│       └── SpecialFileError    # device node, FIFO, socket
├── UnsupportedFeatureError     # recognized but unhandled feature/variant/codec
│                               #   (e.g. a ZIP codec stdlib zipfile lacks, an
│                               #   AES-encrypted ZIP, the 7z BCJ2 coder)
├── PackageNotInstalledError    # a required optional package or external tool is
│                               #   absent (codec backend, crypto backend, unrar)
└── UnsupportedOperationError   # API misuse: operation not valid for this reader's mode
                                #   (e.g. random access on a sequential reader)
```

`UnsupportedFeatureError` and `PackageNotInstalledError` may be raised at open or read
time, so they are top-level `ArchiveyError` subtypes rather than nested under
`OpenError`/`ReadError`. `StreamNotSeekableError` is an **open-time** failure (the source
cannot `seek()` but the chosen format/backend needs one), so it is a subclass of
`OpenError` — **not** `UnsupportedOperationError`.

**`UnsupportedOperationError` vs `UnsupportedFeatureError` — a deliberate split:**
- `UnsupportedOperationError` = **API misuse**: the caller asked for something this
  reader's *mode* does not permit (random access on a `streaming=True` reader, writing
  through a read-only RAR backend, using a closed reader). It is not caused by the
  archive's contents; the fix is always on the caller's side.
- `UnsupportedFeatureError` = a **valid archive with a feature Archivey does not
  implement** (a ZIP codec stdlib `zipfile` lacks, an AES-encrypted ZIP entry, the 7z
  BCJ2 coder, an unknown coder): nothing the caller does changes it.

**Requirement:** every `ArchiveyError` must carry:
- `message: str` — human-readable explanation
- `source_format: ArchiveFormat | None`
- `archive_name: str | None` — a name identifying the archive (its path, or a `BinaryIO.name`); `None` for an anonymous stream
- `member_name: str | None` — the member being processed, if applicable
- `__cause__` — the original exception (preserved via `raise ... from exc`)

The original traceback must be attached and surfaced by default `traceback.print_exc()` calls. Libraries must never swallow the original exception.

---

## 7. Extraction Filter Contract

### 7.1 Universal constraints (cannot be bypassed, including TRUSTED policy)

1. **Path traversal:** Any `name` component equal to `..` after splitting on `/` → `PathTraversalError`.
2. **Absolute paths:** `name` starting with `/` or a Windows drive letter (`C:\`, `\\`) → `PathTraversalError`.
3. **Null bytes:** `name` containing `\x00` → `PathTraversalError`.
4. **Symlink escape:** For SYMLINK members, resolve the target relative to the eventual extraction path. If resolution escapes the `dest` root (after fully resolving all symlink chains) → `SymlinkEscapeError`. This check is re-validated at extraction time, not just at planning time. The resolution is wrapped in a guard: an adversarial symlink **loop** (`a → b`, `b → a`) makes the OS reject resolution with `ELOOP` (`OSError`/`RuntimeError`), which is caught and treated as an escape → `SymlinkEscapeError`, so a cyclic-symlink archive fails safe instead of crashing the extractor.
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

The four limits below live on the `ExtractionLimits` frozen dataclass. It is supplied per
call via `extract(..., limits=)` / `extract_all(..., limits=)`, or as the app-wide default
via `config.extraction_limits`; precedence is per-call `limits` > `config.extraction_limits`
> the library default. `ExtractionLimits.UNLIMITED` disables all four guards for explicitly
trusted archives.

All extraction paths must track bytes written and raise `ExtractionError` when:
- **Cumulative** bytes written across all members exceeds `max_extracted_bytes` (default: 2 GiB; caller-configurable).
- A **single member's** output / `compressed_size` exceeds `max_ratio` (default: 1000:1; caller-configurable) — computed against that member's own output, not the cumulative total.
- The number of members **actually written** exceeds `max_entries`. Only written members
  count: members excluded by the `members` selector, skipped by the `filter`, or rejected
  by the universal safety check create nothing on disk and do not consume the budget.

**Ratio activation floor.** The ratio check is evaluated **only after** a member's output
exceeds a `ratio_activation_threshold` (default: 5 MiB; caller-configurable). This prevents
false positives on tiny but legitimately highly-compressible files — a 10-byte source
expanding to 15 KiB is a 1500:1 ratio yet harmless, whereas a real bomb expands to hundreds
of MiB or GiB and trips the ratio only after crossing the floor. The `BombTracker` is given
the **original** member (not a filter copy) so `compressed_size` and any late-bound fields
are the accurate source values.

These limits apply only during `extract` / `extract_all`. `read()` and `open()` return raw data and leave bomb detection to the caller.

---

## 8. Format Detection

### 8.1 Algorithm

1. Read up to `DETECTION_LIMIT` bytes (default 4 096 bytes) from the source.
2. Match against the magic-byte table (exact offsets, no heuristics).
3. On a match: return `FormatInfo(confidence=DetectionConfidence.CERTAIN, detected_by="magic")`.
4. On no match: attempt extension-based guess if source is a `Path`; return `confidence=DetectionConfidence.GUESS, detected_by="extension"`.
5. On conflict between magic and extension: magic wins; a `logging.WARNING` is emitted.
6. The bytes read during detection are **never** discarded — seekable streams are `seek(0)`'d back; for non-seekable streams `open_archive()` (not `detect_format()`) supplies a `PeekableStream` wrapper that replays the buffered bytes transparently.

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

Wrapping a non-seekable source is the **opener's** responsibility, not
`detect_format()`'s, so one wrapper is shared by detection and the backend rather than
detection consuming bytes the caller can no longer reach. `detect_format()` itself
consumes nothing: it inspects bytes through `peek()`.

- For **paths and seekable streams**: detection reads via `peek`/`read` and restores the
  position with `seek(0)`; no wrapper is needed.
- For **non-seekable streams**: `open_archive()` SHALL wrap the source in a
  `PeekableStream` **before** running detection and pass that *same* `PeekableStream` to
  both detection and the backend. (A standalone `detect_format()` on a raw non-seekable
  stream the caller intends to keep reading must be given a `PeekableStream` by the caller,
  since an unwrapped non-seekable stream would lose the peeked prefix; `open_archive()`
  does this internally so high-level callers never wrap by hand.)

`PeekableStream` wraps a non-seekable binary stream:
- Buffers the first `DETECTION_LIMIT` bytes in memory (4 096 by default; 32 774 when ISO detection is triggered).
- Exposes a `.peek(n)` method returning buffered bytes without consuming them.
- Reads drain from the buffer first, then fall through to the underlying stream once the buffer is exhausted, so the backend reads the peeked bytes followed by the rest with no data loss.
- `PeekableStream` is a `BinaryIO`-compatible object passed through to the backend.

---

## 9. Backend Registry

### 9.1 Registration

Reading and writing are separate concerns (7z reading is native while writing needs
`py7zr`; RAR has a reader but no writer), so read and write backends live in **separate
registries**. Read backends register via `register_reader(BackendClass)` and write
backends via `register_writer(BackendClass)` (from `archivey.internal.registry`). Core backends register at import
time; optional, library-backed backends register inside a `try/except ImportError` guard
so an absent dependency simply makes the format unavailable (it never appears in
`list_formats()`/`list_writable_formats()`) rather than crashing the import. The native 7z
and RAR *readers* are always registered (no import guard); RAR member-data reads
additionally require the system `unrar` binary at runtime.

**Detection owns matching.** `detect_format()` is the single authority for *which format*
a source is — it owns the central magic table and special-case probes (SFX scan, inner-TAR
probe, ISO window). Read backends declare their `MAGIC`/`EXTENSIONS` as **data** that the
detector aggregates; backends have no per-backend `detect(peek)` logic. The registry then
maps the detected `ArchiveFormat` to a registered backend.

```python
# Internal API
class BackendRegistry:
    # read side
    def register_reader(self, backend_cls: type[ReadBackend]) -> None: ...
    def reader_for_format(self, format: ArchiveFormat) -> type[ReadBackend]: ...
    # write side (separate registry of write backends)
    def register_writer(self, backend_cls: type[WriteBackend]) -> None: ...
    def writer_for_format(self, format: ArchiveFormat) -> type[WriteBackend]: ...
    # availability
    def list_formats(self) -> list[ArchiveFormat]: ...          # readable formats available now
    def list_writable_formats(self) -> list[ArchiveFormat]: ...
```

If detection yields a format with no registered (available) read backend, the system
raises `UnsupportedFormatError` with an install hint. A format with no registered write
backend is unwritable: `create()` raises `UnsupportedOperationError` (for native-read-only
RAR) or `UnsupportedFormatError` with an install hint (for 7z without `[7z-write]`).

### 9.2 ReadBackend / WriteBackend ABCs

Two abstract base classes — `ReadBackend` and `WriteBackend` — rather than one `Backend`
with an optional write method. A format may have a reader, a writer, both, or (RAR) only a
reader.

```python
class ReadBackend(ABC):
    FORMATS: tuple[ArchiveFormat, ...]      # formats this backend reads
    EXTENSIONS: tuple[str, ...]             # declared as data for the detector
    MAGIC: tuple[tuple[int, bytes], ...]    # (offset, bytes) pairs, consumed by the detector
    REQUIRES_SEEK: bool = False             # if True, non-seekable sources are rejected
    OPTIONAL_DEPENDENCY: str | None = None  # e.g. "pycdlib"

    @abstractmethod
    def open_read(
        self,
        source: Path | BinaryIO,            # a PeekableStream for non-seekable sources
        streaming: bool,
        password: bytes | None,
        encoding: str | None,
        archive_name: str | None,           # computed once by open_archive() (path or stream name)
    ) -> ArchiveReader: ...

class WriteBackend(ABC):
    FORMATS: tuple[ArchiveFormat, ...]      # formats this backend writes
    OPTIONAL_DEPENDENCY: str | None = None  # e.g. "py7zr" for 7z write

    @abstractmethod
    def open_write(
        self,
        dest: Path | BinaryIO,
        compression: CompressionSpec | None,
        password: bytes | None,
        encoding: str | None,
    ) -> ArchiveWriter: ...
```

Each backend is a **stateless factory**: it holds no per-archive state, so multiple readers
can be open simultaneously from one backend class. When a `REQUIRES_SEEK` backend is given a
non-seekable source, `open_read()` raises `StreamNotSeekableError` (a subclass of
`OpenError`).

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

**Non-seekable ZIP:** Since the central directory lives at EOF, a non-seekable ZIP stream cannot be read in the random-access default (`streaming=False`). The backend raises `StreamNotSeekableError` at open time, advising the caller to buffer the source (save to disk or a `BytesIO`) and reopen; the library does **not** implicitly buffer. (Transparent spooling to a `tempfile.SpooledTemporaryFile`, if wanted, would return as an explicit opt-in argument — see the `format-zip` spec's Phase-3 reconciliation note.)

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

**Truncation detection:** After iterating all members, verify that the final 512-byte block(s) are null-filled end-of-archive markers. If not present, emit a `logging.WARNING` and optionally raise `TruncatedError` when `ArchiveyConfig.strict_archive_eof` is `True` (default: warn only; configured via `open_archive(..., config=...)`).

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

### 10.4 7-Zip (native read; write requires `[7z-write]` → `py7zr`)

| Property | Value |
|----------|-------|
| Backend dependency | **Native** header parser + stdlib `lzma`/`bz2`/`zlib`. PPMd/Deflate64 via `[7z]`; AES decryption via `[crypto]`. |
| Listing cost | INDEXED — the header block is parsed upfront |
| Access cost | SOLID (typically); DIRECT only if no solid blocks |
| Supports write | `[7z-write]` (via `py7zr`) |
| Requires seek | Yes |

7z **reading is native** — a native header parser plus the standard library's
`lzma`/`bz2`/`zlib` for the common codecs (zero-dependency core). PPMd and Deflate64 are
available through the `[7z]` extra; AES decryption through `[crypto]`; the BCJ2 coder is
**detected and rejected** with `UnsupportedFeatureError` rather than mis-decoded. `py7zr` is
used **only for 7z writing** (`[7z-write]`) and as a test oracle — never for reading.

Reading is **streaming**: a solid folder is decompressed progressively, yielding members as
the decompressed stream is consumed, so peak memory is the decompressor working state plus
one in-flight chunk — there is **no** per-folder `SpooledTemporaryFile` cache and no
growing decompressed cache held until `close()`. `solid_block_count` is the number of solid
folders; `is_solid` reflects whether members share folders. Per-folder codec info is mapped
to `CompressionAlgo` values; 7z POSIX metadata lives in an optional attribute block (absent
→ `mode`/`uid`/`gid` are `None`). Deeper internals are specified in `format-7z/spec.md`.

### 10.5 RAR (native metadata read; member data via system `unrar`)

| Property | Value |
|----------|-------|
| Backend dependency | **Native** RAR3/RAR5 metadata parser + the external `unrar` binary for member-data decompression. Encrypted RAR5 headers decrypted via `[crypto]`. |
| Listing cost | INDEXED — metadata parsed upfront (REQUIRES_SCANNING when no quick-open record) |
| Access cost | SOLID if solid archive; DIRECT otherwise |
| Supports write | No — RAR is proprietary; read-only |
| Requires seek | Yes |

RAR **metadata is read by a native RAR3/RAR5 parser** (no `rarfile`); encrypted RAR5
headers are decrypted natively via `[crypto]`, so **listing** works without any external
tool. Member-**data** decompression is delegated to the external `unrar` binary; a data read
without `unrar` raises a clear `PackageNotInstalledError` naming the missing tool. `rarfile`
is only a test oracle.

Solid-archive member access **streams**: the decompressed stream is consumed progressively,
with bounded memory, the same way `stream_members()` works for other solid formats — no
one-shot `unrar`-to-tempdir extraction and no per-file subprocess fan-out. Deeper internals
(volume joining, RAR3 vs RAR5 details) are in `format-rar/spec.md`.

**RAR4 vs RAR5 timestamp handling:**
- RAR4: stores local wall-clock time → naive `datetime`.
- RAR5: stores UTC with sub-second precision → timezone-aware `datetime`.

**Link handling:** RAR5 stores hardlinks/file-copies and symlinks via the redirect field.
The ABC-level link-following described in §3.2 resolves all of these uniformly across
formats; the native parser surfaces the link type and target.

**Header encryption (RAR5):** `ArchiveInfo.is_encrypted = True`; listing requires the password.

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

A plain filesystem directory is treated as a zero-cost pseudo-archive: `ListingCost.INDEXED`, `AccessCost.DIRECT`, fully seekable. Useful for conversion pipelines where the "source" is an existing directory.

---

## 11. Writing and Conversion

### 11.1 CompressionSpec

The `algo` field is **nullable** (`None` = let the backend choose the algorithm appropriate
for the format and level), and `level` accepts either a numeric value **or** a
format-agnostic `CompressionLevel` enum, so callers can ask for relative effort without
knowing a format's numeric scale.

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

**Resolution table** (how `(algo, level)` is resolved by the backend). `compression=None` at
`create()`/`add_*` is equivalent to `CompressionSpec(algo=None, level=DEFAULT)`.

| `algo` | `level` | Behavior |
|--------|---------|----------|
| `None` | `STORE`/`FAST`/`DEFAULT`/`MAX` | Backend **chooses the algorithm** appropriate for the format and requested effort (a higher level MAY select a different algorithm), then applies that effort. `STORE` selects `STORED`. |
| `None` | numeric `int` | Backend uses the format's **default algorithm** at the given numeric level. |
| set | `STORE` | Resolves to `STORED`; the explicit `algo` is overridden and a `logging.WARNING` notes the contradiction. |
| set | `FAST`/`DEFAULT`/`MAX` | Uses that algorithm, mapping the symbolic level to that algorithm's nearest concrete level. |
| set | numeric `int` | Uses that algorithm at that numeric level. Out-of-range raises `ValueError` (no silent clamp). |

**Fail fast on an unavailable codec.** When the caller names an **explicit** `algo` whose
backend is not installed (e.g. `algo=CompressionAlgo.ZSTD` without the `[zstd]` extra), or a
codec the target format cannot represent, `create()` (or the first `add_*` that would use it)
**raises immediately** — `PackageNotInstalledError` for a missing package/tool,
`UnsupportedFeatureError` for an unrepresentable codec. It MUST NOT silently substitute a
different algorithm or degrade to the format default. (This applies only to an explicit
`algo`; with `algo=None` the backend is choosing and SHALL pick an available algorithm.)

### 11.2 Conversion semantics

`writer.add_members(reader)` must:
1. Drive `reader.stream_members()` (consuming the source sequentially, respecting solid-archive bounded-memory semantics).
2. For each yielded `(member, stream)` pair, pipe the data stream into the writer.
3. Translate the `ArchiveMember` metadata (name, mode, timestamps) directly — no re-encoding.
4. Skip members with types unsupported by the target format, emitting `logging.WARNING`.
5. Not buffer the full archive in memory; pipe at a configurable chunk size (default: 1 MiB).

---

## 12. Progress Reporting

```python
@dataclass
class ExtractionProgress:
    member: ArchiveMember
    bytes_written: int
    total_bytes_estimated: int | None   # None if archive has no size info
    members_done: int
    members_total: int | None

@dataclass
class ExtractionResult:
    member: ArchiveMember
    path: Path | None            # the written path, or None if not written
    status: ExtractionStatus
    error: ArchiveyError | OSError | None = None   # the failure, for FAILED/REJECTED under
                                         # OnError.CONTINUE; an OSError when it is a
                                         # filesystem read/write error on this member

class ExtractionStatus(Enum):
    EXTRACTED = "extracted"
    SKIPPED   = "skipped"       # pre-existing destination, under OverwritePolicy.SKIP
    REJECTED  = "rejected"      # blocked by a safety filter (under OnError.CONTINUE)
    FAILED    = "failed"        # error while extracting (under OnError.CONTINUE)
```

Under the default `OnError.STOP`, the first rejection/failure raises and halts; under
`OnError.CONTINUE` the failing member is recorded as `REJECTED`/`FAILED` (with `error`)
and extraction proceeds — the returned list is the report. See §5.3 and §7.

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

The test suite must demonstrate that extracting a canonical directory structure (files, symlinks, nested dirs, empty dirs, filenames with unicode and spaces) produces **identical** `ArchiveMember` objects from ZIP, TAR, 7z, RAR, and ISO sources (modulo documented format limitations). Equivalence is defined as field-by-field equality excluding `raw_name`, `compressed_size`, `hashes`, and `extra`.

### 14.2 Adversarial corpus

The adversarial test corpus must include:
- **Zip bomb:** quine-style and nested (42.zip variant) — verify `max_ratio` and `max_extracted_bytes` limits.
- **Ratio-floor false-positive guard:** a tiny but highly-compressible legitimate file (e.g. 10 bytes → 15 KiB, 1500:1) — verify it extracts **without** error because its output stays under `ratio_activation_threshold`.
- **Path traversal:** `../evil`, `../../etc/passwd`, `./../../outside` — verify `PathTraversalError`.
- **Absolute paths:** `/etc/passwd`, `C:\Windows\System32\evil.dll` — verify `PathTraversalError`.
- **Symlink escape:** symlink pointing to `../../outside`, and chained symlinks — verify `SymlinkEscapeError`.
- **Symlink loop:** cyclic symlinks (`a → b`, `b → a`) — verify extraction fails safe with `SymlinkEscapeError` (no uncaught `OSError`/crash).
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
with archivey.open_archive("archive.tar.gz") as ar:
    print(ar.info)                      # format, solid, cost receipt
    for member in ar:
        print(member.name, member.size, member.type)
```

### 15.2 Computing file hashes without writing to disk

Use `stream_members()` — it yields `(member, stream)` pairs in a single pass with bounded memory. Decompression is streaming: a solid block is decompressed progressively as its members are consumed, so peak memory is the decompressor state plus one in-flight chunk.

```python
import archivey
import hashlib

with archivey.open_archive("archive.7z") as ar:
    for member, f in ar.stream_members():
        if member.type != archivey.MemberType.FILE:
            continue
        h = hashlib.sha256()
        while chunk := f.read(65536):
            h.update(chunk)
        print(f"{h.hexdigest()}  {member.name}")
```

The same pattern works for any per-file sequential processing: MIME-type sniffing, line counting, full-text indexing, virus scanning. Prefer this (or `open()`) over `read()` for anything large: `read(member)` would pull the member's **entire** decompressed payload into RAM at once, whereas the loop above holds only one 64 KiB chunk.

`ar.open(member)` in a `for member in ar` loop also works and is correct, but on a solid archive each `open()` re-decompresses the member's block from its start (emitting a `logging.WARNING`) — no growing cache is held, but the CPU cost adds up. Use `open()` when you need random access or may revisit members; use `stream_members()` when you're doing a single sequential pass.

### 15.3 Opening a symlink or hardlink

`open()` and `read()` transparently follow links that point to other members in the same archive, regardless of format. This mirrors `tarfile.extractfile()` behavior.

```python
with archivey.open_archive("archive.tar") as ar:
    # Suppose the archive contains:
    #   data/v1.0/report.txt  (regular file)
    #   data/latest           (symlink -> v1.0/report.txt)
    #   data/also-latest      (hardlink -> data/v1.0/report.txt)

    # All three produce the same bytes:
    content_a = ar.read("data/v1.0/report.txt")
    content_b = ar.read("data/latest")        # follows symlink
    content_c = ar.read("data/also-latest")   # follows hardlink
    assert content_a == content_b == content_c

    # The ArchiveMember object itself still reflects the link type:
    link_member = ar["data/latest"]
    assert link_member.type == archivey.MemberType.SYMLINK
    assert link_member.link_target == "v1.0/report.txt"
```

If a link's target is not present in the archive (e.g. an external symlink), `open()` raises `LinkTargetNotFoundError`. Link chains are followed recursively with cycle detection (a revisited member raises `ReadError`); there is no fixed depth limit.

### 15.4 Format conversion (streaming, no intermediate file)

```python
import archivey

# Convert tar.gz to zip — add_members() drives stream_members() internally
with archivey.open_archive("input.tar.gz") as reader, \
     archivey.create("output.zip") as writer:
    writer.add_members(reader)

# Select on the reader, transform/rename on the writer — one streaming pass, no reopen:
with archivey.open_archive("input.tar.gz") as reader, \
     archivey.create("output.zip") as writer:
    writer.add_members(
        reader.stream_members(lambda m: m.name.endswith(".py")),   # selection
        filter=lambda m: m.replace(name="src/" + m.name),          # transform/rename
    )
```

`add_members()` internally calls `reader.stream_members()`, so it gets the bounded-memory path for solid archives automatically.

### 15.5 Checking the cost receipt before committing

```python
import archivey
from archivey import AccessCost

with archivey.open_archive("mystery.7z") as ar:
    if ar.cost.access_cost == AccessCost.SOLID:
        # Solid archive: use stream_members() for bounded memory.
        # Random access via open() works but re-decompresses each member's block.
        print(f"Solid: {ar.cost.solid_block_count} block(s). Using stream_members().")
        for member, stream in ar.stream_members():
            process(member, stream)
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
    writer.add_file("src/", name="source/")
```

---

## Appendix A: Deferred / Out-of-Scope Items

These were considered and deliberately excluded from v1:

- **In-place modification:** Archive append/update is architecturally incompatible with ZIP and 7z and adds significant complexity. Omitted.
- **Encryption write for 7z/RAR:** RAR write is proprietary; 7z encryption write via `py7zr` may be added as a `[7z-write]` feature.
- **Native sparse file extraction:** Sparse file support in TAR is detected and flagged but extracted as dense files to avoid cross-platform filesystem complexity.
- **NTFS junction recreation on non-Windows:** Junction points are presented as `MemberType.SYMLINK` with `extra["zip.is_junction"] = True` but recreated only on Windows.
- **Async API:** A purely synchronous API is specified for v1. An async wrapper layer is a natural follow-on.

(Multi-volume **joining** is *not* deferred — `format-rar` and `format-7z` specify reading a split set as one logical archive; `ArchiveInfo.is_multivolume` still reports the status. See §3.1 and those format specs.)
