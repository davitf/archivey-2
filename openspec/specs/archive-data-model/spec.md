# Archive Data Model

## Purpose

Defines the core data types that flow through the entire Archivey public API: the compositional `ArchiveFormat` `(container, stream)` model and member-type enumerations, the compression algorithm model, the mutable `ArchiveMember` dataclass that represents a single archive entry, and the `ArchiveInfo` dataclass that carries archive-level metadata. These types are the shared contract between readers, writers, backends, and callers.
## Requirements
### Requirement: Archive format identity (ArchiveFormat)

The system SHALL model a format as the combination of a **container** and a **stream**
codec, rather than a single flat enum. `ContainerFormat` names the member layout (zip,
tar, 7z, …) and `StreamFormat` names the outer single-stream codec the container is
wrapped in (gzip, xz, … or `UNCOMPRESSED`). `ArchiveFormat` is a frozen
`(container, stream)` dataclass; the familiar named formats (`ZIP`, `TAR_GZ`,
`SEVEN_Z`, …) are predefined class-var instances, so callers keep writing
`ArchiveFormat.TAR_GZ` while the model underneath is compositional.

`StreamFormat` SHALL cover every outer single-stream codec the library can read as a
standalone stream, so that single-file and `tar.<codec>` formats are all expressible:

```python
class StreamFormat(StrEnum):
    UNCOMPRESSED  = "uncompressed"
    GZIP          = "gz"
    BZIP2         = "bz2"
    XZ            = "xz"
    ZSTD          = "zst"     # requires [zstd] extra
    LZ4           = "lz4"     # requires [lz4] extra
    LZIP          = "lz"
    ZLIB          = "zz"
    BROTLI        = "br"
    UNIX_COMPRESS = "Z"       # requires [unix-compress] extra
    # extensible: further outer codecs are added here
```

Named standalone `ArchiveFormat` constants SHALL exist for the bare single-stream
formats — `GZ`, `BZ2`, `XZ`, `ZST`, `LZ4`, `LZIP`, `ZLIB`, `BROTLI`, `Z` (each
`RAW_STREAM × <codec>`) — alongside the container constants (`ZIP`, `TAR`, `TAR_GZ`, …).
Container × codec combinations that are **not in common practice** (e.g. `tar.lz`,
`tar.br`) SHALL NOT get a predefined constant; they are constructed on demand as
`ArchiveFormat(container, stream)` and compare equal to any other instance with the same
pair. `file_extension()` derives from the codec for a `RAW_STREAM` (e.g. `Z` → `"Z"`,
`LZIP` → `"lz"`) and from `container.codec` for a container (e.g. `TAR × LZIP` → `"tar.lz"`).

#### Scenario: format identity round-trips through the (container, stream) pair

- **WHEN** a caller compares `ar.format` against `ArchiveFormat(ContainerFormat.TAR, StreamFormat.GZIP)`
- **THEN** it is equal to `ArchiveFormat.TAR_GZ`

#### Scenario: a standalone lzip stream has a named format

- **WHEN** a `.lz` (lzip) stream is opened
- **THEN** `ar.format == ArchiveFormat.LZIP`, whose `container == ContainerFormat.RAW_STREAM` and `stream == StreamFormat.LZIP`

#### Scenario: an uncommon container×codec combination is built on demand

- **WHEN** a `tar.lz` (tar wrapped in lzip) source is opened
- **THEN** `ar.format == ArchiveFormat(ContainerFormat.TAR, StreamFormat.LZIP)` even though no `TAR_LZIP` class constant is predefined
- **AND** `ar.format.file_extension() == "tar.lz"`

### Requirement: ArchiveMember type taxonomy (MemberType)

The system SHALL define a `MemberType` enum describing the kind of filesystem object each archive member represents.

```python
class MemberType(Enum):
    FILE      = "file"
    DIRECTORY = "directory"
    SYMLINK   = "symlink"       # includes Windows junction (flagged via extra["is_junction"])
    HARDLINK  = "hardlink"
    OTHER     = "other"         # device nodes, FIFOs, sockets — extraction always rejected
```

Windows NTFS junction points SHALL be surfaced as `MemberType.SYMLINK` with `extra["is_junction"] = True`. Members of type `OTHER` SHALL always be rejected during extraction regardless of policy.

#### Scenario: device node is classified as OTHER

- **WHEN** a TAR archive contains a device node or FIFO
- **THEN** the corresponding `ArchiveMember` has `type == MemberType.OTHER`

#### Scenario: Windows junction surfaced as SYMLINK

- **WHEN** a ZIP archive contains a Windows junction point
- **THEN** the corresponding `ArchiveMember` has `type == MemberType.SYMLINK` and `extra["is_junction"] == True`

---

### Requirement: Compression method model

The system SHALL define a `CompressionAlgorithm` enum covering all recognized codecs, a `CompressionMethod` frozen dataclass holding a single codec with its level and raw properties, and SHALL represent multi-codec filter chains as `tuple[CompressionMethod, ...]`.

```python
class CompressionAlgorithm(Enum):
    STORED    = "stored"
    DEFLATE   = "deflate"
    DEFLATE64 = "deflate64"
    BZIP2     = "bzip2"
    LZMA      = "lzma"
    LZMA2     = "lzma2"
    ZSTD      = "zstd"
    LZ4       = "lz4"
    BROTLI    = "brotli"
    PPMD      = "ppmd"
    BCJ       = "bcj"           # x86 executable filter
    BCJ2      = "bcj2"
    DELTA     = "delta"
    UNKNOWN   = "unknown"       # unrecognized codec ID
    # This enum is extensible: as new formats/codecs are supported, members are
    # appended here. A codec Archivey does not recognize maps to UNKNOWN (never an
    # exception), so callers SHOULD treat the set as open-ended.

@dataclass(frozen=True)
class CompressionMethod:
    algo: CompressionAlgorithm
    level: int | None = None        # compression level if known
    properties: bytes | None = None # raw codec properties blob
```

A `tuple[CompressionMethod, ...]` models a filter chain. For example, a typical 7z executable entry uses `(CompressionMethod(BCJ2), CompressionMethod(LZMA2))`. An unrecognized codec SHALL be mapped to `CompressionAlgorithm.UNKNOWN` rather than raising an exception.

#### Scenario: single-codec member

- **WHEN** a ZIP member is stored with DEFLATE compression
- **THEN** `member.compression == (CompressionMethod(algo=CompressionAlgorithm.DEFLATE),)`

#### Scenario: filter-chain member

- **WHEN** a 7z member uses a BCJ2 + LZMA2 filter chain
- **THEN** `member.compression == (CompressionMethod(CompressionAlgorithm.BCJ2), CompressionMethod(CompressionAlgorithm.LZMA2))`

#### Scenario: unrecognized codec

- **WHEN** an archive contains a codec ID that Archivey does not recognize
- **THEN** the codec is mapped to `CompressionAlgorithm.UNKNOWN` and no exception is raised

---

### Requirement: The ArchiveMember record

The system SHALL define `ArchiveMember` as a dataclass representing one archive entry. All fields that the format cannot provide SHALL be `None`; the library MUST NOT substitute silent defaults or guesses.

`ArchiveMember` is **mutable** (not frozen). Several fields are genuinely unknown when
a member is first yielded and only become known once its data has been read — the
final `size`/CRC of a gzip stream or a ZIP data-descriptor entry, or a `link_target`
that is stored in (or encrypted within) the member's *data* rather than its header.
The library fills these fields **in place** as it streams, so the `ArchiveMember` a
caller already holds gains its late values without a re-fetch. This is required for
`streaming=True` mode, where the member list cannot be materialized and re-read.

Because the object is mutable, the contract is: **callers MUST treat an
`ArchiveMember` as read-only.** The library is the only writer. A caller (or an
extraction/iteration filter) that needs an altered member SHALL call `.replace(**kwargs)`,
which returns a **copy** with the changes applied and never mutates the original. As a
consequence, `ArchiveMember` is **not hashable** (a mutable value object must not be a
dict key or set element); callers key by `member.name` or `member.member_id` instead.

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
                                            #   or None when the target is unknown, not yet resolved
                                            #   (streaming), or absent from the archive

    # --- Compression ---
    compression: tuple[CompressionMethod, ...] = ()

    # --- Flags ---
    is_encrypted: bool = False
    is_sparse: bool = False                 # TAR sparse files; extraction as regular file

    # --- Provenance / extra metadata (None when the format does not record it) ---
    comment: str | None = None              # per-member comment (some formats)
    create_system: "CreateSystem | None" = None   # OS that created the entry (ZIP create_system
                                            #   values: FAT/UNIX/NTFS/…; other formats map where they can)
    windows_attrs: int | None = None        # Windows FILE_ATTRIBUTE_* bitmask, if recorded

    # --- Integrity ---
    # Per-algorithm digests, keyed by lowercase algorithm name. CRC32 values are
    # ints ("crc32"); cryptographic/other digests are raw bytes ("blake2sp",
    # "sha256", ...). Empty when the format records no integrity data. A format
    # MUST NOT report one algorithm's value under another's key (e.g. a RAR5
    # Blake2sp hash is "blake2sp", never "crc32"). Excluded from __eq__.
    hashes: Mapping[str, int | bytes] = field(default_factory=dict, compare=False)

    # --- Format-specific overflow ---
    # Keys are namespaced: "zip.extra_fields", "tar.pax_headers", "iso.rock_ridge", etc.
    # Excluded from __eq__: format-specific extras don't affect logical identity.
    extra: dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def member_id(self) -> int: ...         # stable 0-based position in the source archive,
                                            #   assigned at registration; identity for de-dup/ordering
    @property
    def archive_id(self) -> str: ...        # id of the archive this member belongs to; used to
                                            #   validate a member passed back into its own reader

    # --- Read-only convenience helpers (derived from `type`/`extra`; never settable) ---
    @property
    def is_file(self) -> bool: ...          # type == FILE
    @property
    def is_dir(self) -> bool: ...           # type == DIRECTORY
    @property
    def is_link(self) -> bool: ...          # type in (SYMLINK, HARDLINK)
    @property
    def is_other(self) -> bool: ...         # type == OTHER
    @property
    def is_junction(self) -> bool: ...      # SYMLINK with extra["is_junction"] is True

    def replace(self, **kwargs: Any) -> "ArchiveMember":
        """Return a *copy* with the given fields changed; never mutates self.
        Filters use this to sanitize/rename a member without touching the original."""
```

`ArchiveMember` is mutable so the library can complete metadata during a streaming
pass; the `hashes` and `extra` fields are excluded from `__eq__` (integrity digests
vary by format and would break cross-format equivalence; format-specific extras do not
affect logical identity). There is no `crc32` field or accessor — callers read
`member.hashes.get("crc32")`. The object is **not hashable**.

The `is_file`/`is_dir`/`is_link`/`is_other`/`is_junction` helpers and `comment` /
`create_system` (a `CreateSystem` enum mirroring ZIP's create-system values:
FAT/UNIX/NTFS/…) / `windows_attrs` metadata fields are carried for ergonomics and
fidelity. Archivey deliberately does **not** expose `zipfile`-compatibility aliases
(`date_time`, `CRC`, a naive-`mtime` alias) — it is not impersonating `zipfile`; callers
use `modified`, `hashes["crc32"]`, and the `MemberType` enum directly.

#### Scenario: unavailable field is None

- **WHEN** a format cannot provide a field (e.g. GZ does not reliably provide uncompressed size up front)
- **THEN** the corresponding `ArchiveMember` field is `None`, not a default value

#### Scenario: late fields are filled in place while streaming

- **WHEN** a member's `size`/`link_target` is unknown at the moment it is first yielded but becomes known after its data is read during the same streaming pass
- **THEN** the library fills that field **in place** on the same `ArchiveMember` object the caller holds, without requiring a re-fetch

#### Scenario: callers edit via copy, not mutation

- **WHEN** a filter needs to rename or sanitize a member
- **THEN** it calls `member.replace(name=...)` to obtain an edited copy, and the original `ArchiveMember` is left unchanged

#### Scenario: ArchiveMember is not hashable

- **WHEN** code attempts to place an `ArchiveMember` in a `set` or use it as a dict key
- **THEN** the operation fails (the type is unhashable); callers key by `member.name` or `member.member_id` instead

#### Scenario: integrity digests under their algorithm keys

- **WHEN** a ZIP member records a CRC32 and a RAR5 member records only a Blake2sp hash
- **THEN** the ZIP member has `hashes["crc32"]` as an int and no `"blake2sp"` key, and the RAR5 member has `hashes["blake2sp"]` as bytes and no `"crc32"` key

---

### Requirement: ArchiveMember name normalization rules

The system SHALL normalize `ArchiveMember.name` according to a deterministic set of rules, while preserving the verbatim stored bytes in `ArchiveMember.raw_name`. When normalization changes the logical path, a warning SHALL be emitted via the `archivey.normalization` logger.

Normalization rules applied in order:
1. Replace all `\` with `/`.
2. Strip leading `/` and `./`.
3. Collapse `//` and `foo/../bar` sequences.
4. Append `/` for directory members if not already present.
5. Never produce an empty string — the root directory becomes `"."`.

`name` is produced by decoding the stored bytes (using the format's internal encoding
signal where present, otherwise the resolved/auto-detected `encoding`) and then applying
the rules above.

`raw_name` holds **exactly what the archive stored** for the member's name — the
verbatim, encoded, pre-normalization bytes — so the name can be re-decoded losslessly
under a different encoding; it is `None` only when the format exposes no separate raw
form. For formats where the logical `name` is **not** taken from archive content but
derived elsewhere (a single-file compressor, whose `name` comes from the *source
filename*), `raw_name` still holds the archive's stored name when one exists — e.g. a
gzip stream's `FNAME` bytes — so `raw_name` may legitimately differ from a value
`name` would decode to. Treating the source-filename derivation as the "normalization"
step for these formats keeps one rule: `raw_name` is ground truth, `name` is the
normalized presentation.

#### Scenario: backslash conversion

- **WHEN** an archive member is stored with the name bytes `b"foo\\bar\\baz.txt"`
- **THEN** `member.name == "foo/bar/baz.txt"` and `member.raw_name == b"foo\\bar\\baz.txt"`

#### Scenario: traversal sequence collapsed

- **WHEN** an archive member has the name `"foo/../bar"`
- **THEN** `member.name == "bar"` and a warning is emitted via `archivey.normalization`

#### Scenario: raw_name carries the stored name even when name is source-derived

- **WHEN** a `.gz` stream stores `FNAME = "report.csv"` and is opened from a path `archive.gz`
- **THEN** `member.raw_name` holds the undecoded `FNAME` bytes while `member.name == "archive"` (from the source filename), and the decoded `FNAME` is also available in `member.extra["gzip.original_filename"]`

### Requirement: Archive-level metadata (ArchiveInfo)

The system SHALL define an `ArchiveInfo` frozen dataclass that carries archive-level
descriptive metadata, available immediately after `open_archive()` without triggering a
full member scan. In addition to the core descriptive fields (`format`, `format_version`,
`is_solid`, `member_count`, `comment`, `is_encrypted`, `is_multivolume`, `cost`),
`ArchiveInfo` SHALL carry an `extra` mapping for **format-specific archive-level
metadata**, mirroring `ArchiveMember.extra`:

```python
@dataclass(frozen=True)
class ArchiveInfo:
    format: ArchiveFormat
    format_version: str | None
    is_solid: bool
    member_count: int | None        # None if a count requires a full scan
    comment: str | None
    is_encrypted: bool              # header-level encryption (7z, RAR5)
    is_multivolume: bool
    cost: CostReceipt
    extra: dict[str, Any] = field(default_factory=dict, compare=False)
```

Keys in `extra` SHALL be namespaced strings (e.g. `"iso.namespace"`), and `extra` SHALL be
excluded from `__eq__` (format-specific archive metadata does not affect logical identity),
matching `ArchiveMember.extra`. `member_count` SHALL be `None` when the format has no
central directory and a count would require scanning the whole archive.

#### Scenario: member_count is None for a scan-only format

- **WHEN** a TAR archive (no central directory) is opened
- **THEN** `ar.info.member_count` is `None`

#### Scenario: format-specific archive metadata is exposed via extra

- **WHEN** an ISO 9660 image whose richest namespace is Joliet is opened
- **THEN** `ar.info.extra["iso.namespace"] == "joliet"`

