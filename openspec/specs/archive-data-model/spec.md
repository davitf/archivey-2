# Archive Data Model

## Purpose

Defines the core data types that flow through the entire Archivey public API: the format and member-type enumerations, the compression algorithm model, the `Member` frozen dataclass that represents a single archive entry, and the `ArchiveInfo` dataclass that carries archive-level metadata. These types are the shared contract between readers, writers, backends, and callers.

## Requirements

### Requirement: Archive format identity (ArchiveFormat)

The system SHALL define an `ArchiveFormat` enum that uniquely identifies each supported archive or compression format. Formats that require an optional extra package are noted below.

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
    SEVEN_Z   = "7z"          # read: native (core); write: [7z-write] extra
    RAR       = "rar"         # read: native metadata + system `unrar` for data
    ISO       = "iso"         # requires [iso] extra
    DIRECTORY = "directory"   # plain filesystem directory
```

#### Scenario: format identity in reader metadata

- **WHEN** an archive is opened successfully
- **THEN** `ar.format` returns the `ArchiveFormat` enum value matching the detected or specified format

---

### Requirement: Member type taxonomy (MemberType)

The system SHALL define a `MemberType` enum describing the kind of filesystem object each archive member represents.

```python
class MemberType(Enum):
    FILE      = "file"
    DIRECTORY = "directory"
    SYMLINK   = "symlink"       # includes Windows junction (flagged via extra["is_junction"])
    HARDLINK  = "hardlink"
    OTHER     = "other"         # device nodes, FIFOs, sockets — extraction always rejected
```

Windows NTFS junction points SHALL be surfaced as `MemberType.SYMLINK` with `extra["zip.is_junction"] = True`. Members of type `OTHER` SHALL always be rejected during extraction regardless of policy.

#### Scenario: device node is classified as OTHER

- **WHEN** a TAR archive contains a device node or FIFO
- **THEN** the corresponding `Member` has `type == MemberType.OTHER`

#### Scenario: Windows junction surfaced as SYMLINK

- **WHEN** a ZIP archive contains a Windows junction point
- **THEN** the corresponding `Member` has `type == MemberType.SYMLINK` and `extra["zip.is_junction"] == True`

---

### Requirement: Compression method model

The system SHALL define a `CompressionAlgo` enum covering all recognized codecs, a `CompressionMethod` frozen dataclass holding a single codec with its level and raw properties, and SHALL represent multi-codec filter chains as `tuple[CompressionMethod, ...]`.

```python
class CompressionAlgo(Enum):
    STORED    = "stored"
    DEFLATE   = "deflate"
    DEFLATE64 = "deflate64"
    BZIP2     = "bzip2"
    LZMA      = "lzma"
    LZMA2     = "lzma2"
    ZSTD      = "zstd"
    LZ4       = "lz4"
    PPMD      = "ppmd"
    BCJ       = "bcj"           # x86 executable filter
    BCJ2      = "bcj2"
    DELTA     = "delta"
    UNKNOWN   = "unknown"       # unrecognized codec ID

@dataclass(frozen=True)
class CompressionMethod:
    algo: CompressionAlgo
    level: int | None = None        # compression level if known
    properties: bytes | None = None # raw codec properties blob
```

A `tuple[CompressionMethod, ...]` models a filter chain. For example, a typical 7z executable entry uses `(CompressionMethod(BCJ2), CompressionMethod(LZMA2))`. An unrecognized codec SHALL be mapped to `CompressionAlgo.UNKNOWN` rather than raising an exception.

#### Scenario: single-codec member

- **WHEN** a ZIP member is stored with DEFLATE compression
- **THEN** `member.compression == (CompressionMethod(algo=CompressionAlgo.DEFLATE),)`

#### Scenario: filter-chain member

- **WHEN** a 7z member uses a BCJ2 + LZMA2 filter chain
- **THEN** `member.compression == (CompressionMethod(CompressionAlgo.BCJ2), CompressionMethod(CompressionAlgo.LZMA2))`

#### Scenario: unrecognized codec

- **WHEN** an archive contains a codec ID that Archivey does not recognize
- **THEN** the codec is mapped to `CompressionAlgo.UNKNOWN` and no exception is raised

---

### Requirement: The Member record

The system SHALL define `Member` as a frozen dataclass representing one archive entry. All fields that the format cannot provide SHALL be `None`; the library MUST NOT substitute silent defaults or guesses.

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
    # Per-algorithm digests, keyed by lowercase algorithm name. CRC32 values are
    # ints ("crc32"); cryptographic/other digests are raw bytes ("blake2sp",
    # "sha256", ...). Empty when the format records no integrity data. A format
    # MUST NOT report one algorithm's value under another's key (e.g. a RAR5
    # Blake2sp hash is "blake2sp", never "crc32"). Excluded from __hash__/__eq__.
    hashes: Mapping[str, int | bytes] = field(default_factory=dict, hash=False, compare=False)

    # --- Format-specific overflow ---
    # Keys are namespaced: "zip.extra_fields", "tar.pax_headers", "iso.rock_ridge", etc.
    # Excluded from __hash__ and __eq__: format-specific extras don't affect logical identity.
    extra: dict[str, Any] = field(default_factory=dict, hash=False, compare=False)
```

`Member` is `frozen=True` for immutability, hashability, and thread-safety. The `hashes` and `extra` fields are excluded from `__hash__` and `__eq__`: integrity digests vary by format (so they would break cross-format equivalence) and format-specific extras do not affect logical identity. There is no `crc32` field or accessor — callers read `member.hashes.get("crc32")`.

#### Scenario: unavailable field is None

- **WHEN** a format cannot provide a field (e.g. GZ does not reliably provide uncompressed size)
- **THEN** the corresponding `Member` field is `None`, not a default value

#### Scenario: Member is hashable and usable as a dict key

- **WHEN** a `Member` object is placed in a `set` or used as a dict key
- **THEN** it hashes and compares correctly based on its identity fields (excluding `hashes` and `extra`)

#### Scenario: integrity digests under their algorithm keys

- **WHEN** a ZIP member records a CRC32 and a RAR5 member records only a Blake2sp hash
- **THEN** the ZIP member has `hashes["crc32"]` as an int and no `"blake2sp"` key, and the RAR5 member has `hashes["blake2sp"]` as bytes and no `"crc32"` key

---

### Requirement: Member name normalization rules

The system SHALL normalize `Member.name` according to a deterministic set of rules, while preserving the original in `Member.original_name`. When normalization changes the logical path, a warning SHALL be emitted via the `archivey.normalization` logger.

Normalization rules applied in order:
1. Replace all `\` with `/`.
2. Strip leading `/` and `./`.
3. Collapse `//` and `foo/../bar` sequences.
4. Append `/` for directory members if not already present.
5. Never produce an empty string — the root directory becomes `"."`.

`original_name` holds the verbatim bytes decoded with the archive's `encoding` parameter, before any normalization.

#### Scenario: backslash conversion

- **WHEN** an archive member has the name `"foo\\bar\\baz.txt"`
- **THEN** `member.name == "foo/bar/baz.txt"` and `member.original_name == "foo\\bar\\baz.txt"`

#### Scenario: leading slash stripped

- **WHEN** an archive member has the name `"/etc/passwd"`
- **THEN** `member.name == "etc/passwd"`

#### Scenario: traversal sequence collapsed

- **WHEN** an archive member has the name `"foo/../bar"`
- **THEN** `member.name == "bar"` and a warning is emitted via `archivey.normalization`

#### Scenario: directory trailing slash appended

- **WHEN** a directory member has the name `"mydir"` without a trailing slash
- **THEN** `member.name == "mydir/"`

#### Scenario: root directory becomes dot

- **WHEN** normalization would produce an empty string (e.g. name was `"/"`)
- **THEN** `member.name == "."`

---

### Requirement: Archive-level metadata (ArchiveInfo)

The system SHALL define an `ArchiveInfo` frozen dataclass that carries archive-level descriptive metadata. It SHALL be available immediately after `open_archive()` without triggering a full member scan.

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

`member_count` SHALL be `None` when the format has no central directory and a count requires scanning the entire archive. `is_encrypted` refers to header-level encryption (as in 7z or RAR5), distinct from per-member encryption indicated by `Member.is_encrypted`. `cost` embeds a `CostReceipt` (defined in the access-intent-and-cost capability) describing listing and access costs.

#### Scenario: member_count is None for streaming formats

- **WHEN** a TAR archive (no central directory) is opened
- **THEN** `ar.info.member_count` is `None`

#### Scenario: is_encrypted reflects header encryption

- **WHEN** a RAR5 archive with header encryption is opened
- **THEN** `ar.info.is_encrypted == True` and listing the archive requires the password
