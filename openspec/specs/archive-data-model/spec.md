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

The system SHALL define the complete mutable, unhashable, caller-read-only
`ArchiveMember` schema as:

```python
@dataclass
class ArchiveMember:
    type: MemberType

    name: str
    raw_name: bytes | None

    size: int | None
    compressed_size: int | None

    modified: datetime | None
    accessed: datetime | None
    created: datetime | None

    mode: int | None
    uid: int | None
    gid: int | None
    uname: str | None
    gname: str | None

    link_target: str | None
    link_target_member: "ArchiveMember | None"

    compression: tuple[CompressionMethod, ...] = ()
    is_encrypted: bool = False
    is_sparse: bool = False

    comment: str | None = None
    create_system: "CreateSystem | None" = None
    windows_attrs: int | None = None

    hashes: Mapping[str, int | bytes] = field(default_factory=dict, compare=False)
    diagnostics: tuple[Diagnostic, ...] = field(default=(), compare=False)
    extra: dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def member_id(self) -> int: ...
    @property
    def archive_id(self) -> str: ...

    @property
    def is_file(self) -> bool: ...
    @property
    def is_dir(self) -> bool: ...
    @property
    def is_link(self) -> bool: ...
    @property
    def is_other(self) -> bool: ...
    @property
    def is_junction(self) -> bool: ...

    def modified_utc(self, tz_for_naive: tzinfo | None = None) -> datetime | None: ...
    def replace(self, **kwargs: Any) -> "ArchiveMember": ...
```

All existing field meanings remain: unavailable values are `None`; `name` follows the
normalization contract while `raw_name` preserves stored bytes; timestamps preserve their
stored timezone semantics; digest keys identify their real algorithms; link targets,
sizes, hashes, and other late-bound values may be filled in place during streaming.
`member_id`/`archive_id` preserve source identity, convenience properties are derived, and
`replace()` creates an edited copy. `hashes`, `diagnostics`, and `extra` are excluded from
equality. There is no `crc32` alias.

`ArchiveMember` is intentionally not frozen because the library may complete metadata
after yielding it. Callers SHALL treat it as read-only, and it SHALL remain unhashable.
The `diagnostics` tuple itself is immutable/read-only, but the library MAY replace that
tuple in place when a later member-specific event occurs. This is not a promise that a
previously returned member is a point-in-time snapshot.

Only occurrences about that concrete member are eligible: initially
`MEMBER_NAME_NORMALIZED`, `MEMBER_TIMESTAMP_INVALID`,
`SYMLINK_TARGET_UNAVAILABLE`, and `DIGEST_UNVERIFIABLE`. Attachment SHALL occur only when
the owning collector first retained the aggregate occurrence and has another shared
retention-budget slot. Aggregate and member values carry the same occurrence id but have
no object-identity guarantee.

Like other late-bound fields, an eligible diagnostic MAY be attached after the member is
yielded. `member.replace()` copies the tuple's current value; caller-created copies do not
consume additional library-retention slots.

`ArchiveInfo` SHALL NOT carry runtime diagnostics. In particular, detection conflict lives
on `FormatInfo`, while runtime scan/rewind/EOF events live on reader/stream summaries.

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

#### Scenario: modified_utc normalizes mixed wall-clock and UTC timestamps

- **WHEN** one member's `modified` is naive (a ZIP DOS wall-clock time) and another's is aware (an NTFS-extra UTC time), and both are passed through `modified_utc()`
- **THEN** both results are timezone-aware UTC datetimes that compare and sort without error, the naive one interpreted in the local timezone (or in `tz_for_naive` when given)
- **AND** `member.modified` itself is unchanged, so `modified.tzinfo is None` still tells the caller the stored value was wall-clock

#### Scenario: integrity digests under their algorithm keys

- **WHEN** a ZIP member records a CRC32 and a RAR5 member records only a Blake2sp hash
- **THEN** the ZIP member has `hashes["crc32"]` as an int and no `"blake2sp"` key, and the RAR5 member has `hashes["blake2sp"]` as bytes and no `"crc32"` key

#### Scenario: normalization attaches under the shared budget

- **WHEN** normalization emits a retained member diagnostic and an attachment slot remains
- **THEN** the member exposes it in `member.diagnostics` with the aggregate occurrence id

#### Scenario: attachment is omitted when only aggregate capacity remains

- **WHEN** a member diagnostic is emitted with one collector budget slot remaining
- **THEN** the aggregate retains the occurrence, `member.diagnostics` does not, and exact counts still include it

#### Scenario: late integrity diagnostic appears in place

- **WHEN** an already-yielded member's stream discovers that its stored digest algorithm cannot be verified
- **THEN** `DIGEST_UNVERIFIABLE` may be appended in place to that same member's diagnostics tuple, budget permitting

#### Scenario: member references remain live in reports

- **WHEN** an `ExtractionReport` contains a result referring to a member and the library later completes a late-bound field on that member
- **THEN** the member field changes in place even though the report's result tuple and diagnostic summary remain immutable

#### Scenario: ArchiveInfo remains an open-time value

- **WHEN** a rewind or missing EOF marker occurs after open
- **THEN** the reader/stream summary changes and frozen `ArchiveInfo` remains unchanged

### Requirement: ArchiveMember name normalization rules

The system SHALL normalize `ArchiveMember.name` using only **meaning-preserving** rules,
while preserving the verbatim stored bytes in `ArchiveMember.raw_name`. Normalization SHALL
NOT perform meaning-altering rewrites — specifically it SHALL NOT strip a leading `/`
(absolute → relative) and SHALL NOT collapse `..` sequences — because those change the path's
meaning and hide an unsafe stored name. A leading `/` and any `..` component are **retained**
in `name`; such names are rejected at extraction time (see `safe-extraction`), not silently
re-rooted at read time. When normalization changes the presented path, a warning SHALL be
emitted via the `archivey.normalization` logger.

Normalization rules applied in order:
1. Replace `\` with `/` **only when the source format/entry uses backslash as a path
   separator** — a `backslash_is_separator` signal the backend supplies. Windows-origin
   entries convert (RAR; ZIP entries whose `create_system` is DOS/Windows — `FAT`,
   `WINDOWS_NTFS`, `VFAT`, `OS2_HPFS`, …); TAR and other POSIX formats keep `\` as a **literal
   filename character** (converting would corrupt a valid POSIX name). This is a separator
   convention, not a safety mechanism (extraction independently treats both separators and
   rejects unsafe paths); the verbatim bytes remain in `raw_name`.
2. Strip a leading `./` and collapse interior `/./` segments.
3. Collapse repeated `//` into a single `/`.
4. Append `/` for directory members if not already present.
5. Never produce an empty string — an empty name or a bare root becomes `"."`.

#### Scenario: backslash converted for a Windows-origin entry

- **WHEN** a Windows-origin member (RAR, or a ZIP entry with a DOS/Windows `create_system`) is
  stored with the name bytes `b"foo\\bar\\baz.txt"`
- **THEN** `member.name == "foo/bar/baz.txt"` and `member.raw_name == b"foo\\bar\\baz.txt"`

#### Scenario: backslash kept literal for a POSIX (TAR) entry

- **WHEN** a TAR member is stored with the name bytes `b"weird\\name.txt"` (backslash is a
  legal POSIX filename character)
- **THEN** `member.name == "weird\\name.txt"` (the backslash is preserved, not treated as a
  separator)

#### Scenario: internal traversal is preserved, not collapsed

- **WHEN** an archive member has the name `"foo/../bar"`
- **THEN** `member.name == "foo/../bar"` (the `..` is retained, not collapsed to `"bar"`)

#### Scenario: absolute path is preserved, not re-rooted

- **WHEN** an archive member is stored as `"/etc/passwd"`
- **THEN** `member.name == "/etc/passwd"` (the leading `/` is retained); it is rejected later
  by `safe-extraction`'s universal path check, not silently converted to `"etc/passwd"`

#### Scenario: escaping traversal is preserved

- **WHEN** an archive member is stored as `"../../etc/passwd"`
- **THEN** `member.name == "../../etc/passwd"` (retained); it is rejected at extraction time

#### Scenario: meaning-preserving cleanups still apply

- **WHEN** an archive member is stored as `"a//b/./c"`
- **THEN** `member.name == "a/b/c"`

#### Scenario: directory trailing slash

- **WHEN** a directory member is stored as `"mydir"`
- **THEN** `member.name == "mydir/"`

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

