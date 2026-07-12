# Archive Data Model

## Purpose

Archivey shares one public data model across readers, writers, backends, and
callers: compositional `ArchiveFormat`, member and compression taxonomies,
mutable `ArchiveMember` records, and frozen archive-level `ArchiveInfo`.

## Related specs

| Spec | Relationship |
| --- | --- |
| `archive-reading` | Reader methods that return and mutate members in place |
| `diagnostics` | Diagnostic values, budgets, and member attachment rules |
| `safe-extraction` | Path safety and rejection of special files |
| `access-mode-and-cost` | `CostReceipt` carried by `ArchiveInfo` |
| `format-detection` | `ArchiveFormat` detection and detection diagnostics |

## Requirements

### Requirement: Archive format identity is compositional

The system SHALL model archive format as a frozen `(container, stream)` pair:
`ContainerFormat` names the member layout (`zip`, `tar`, `7z`, raw stream, ...),
and `StreamFormat` names the outer single-stream codec (`gz`, `xz`, ... or
`UNCOMPRESSED`). Predefined constants such as `ArchiveFormat.ZIP`,
`ArchiveFormat.TAR_GZ`, `ArchiveFormat.SEVEN_Z`, and standalone
`ArchiveFormat.LZIP` SHALL be class-var instances of that pair.

`StreamFormat` SHALL cover every standalone outer codec Archivey can read:
`UNCOMPRESSED`, `GZIP`, `BZIP2`, `XZ`, `ZSTD`, `LZ4`, `LZIP`, `ZLIB`, `BROTLI`,
and `UNIX_COMPRESS`. Standalone raw-stream constants SHALL exist for `GZ`, `BZ2`,
`XZ`, `ZST`, `LZ4`, `LZIP`, `ZLIB`, `BROTLI`, and `Z`. Uncommon container-codec
pairs such as `tar.lz` SHALL be constructed on demand as
`ArchiveFormat(container, stream)` rather than receiving named constants.
`file_extension()` SHALL derive from the stream for raw streams and from
`container.codec` for containers.

#### Scenario: format identity matrix

| Case | Expected |
| --- | --- |
| Compare `ar.format` to `ArchiveFormat(ContainerFormat.TAR, StreamFormat.GZIP)` | Equal to `ArchiveFormat.TAR_GZ` |
| Open a `.lz` standalone lzip stream | `ArchiveFormat.LZIP`; container `RAW_STREAM`; stream `LZIP` |
| Open `tar.lz` | Equal to `ArchiveFormat(ContainerFormat.TAR, StreamFormat.LZIP)`; `file_extension() == "tar.lz"` |

### Requirement: MemberType describes filesystem object kind

The system SHALL define:

```python
class MemberType(Enum):
    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"
    HARDLINK = "hardlink"
    OTHER = "other"
```

Windows NTFS junctions SHALL surface as `MemberType.SYMLINK` with
`extra["is_junction"] == True`. `MemberType.OTHER` SHALL always be rejected by
extraction regardless of policy.

#### Scenario: member type matrix

| Case | Expected |
| --- | --- |
| TAR contains a device node or FIFO | `member.type == MemberType.OTHER` |
| ZIP contains a Windows junction | `member.type == MemberType.SYMLINK`; `member.extra["is_junction"] is True` |

### Requirement: Compression methods model codec chains

The system SHALL define `CompressionAlgorithm`, frozen `CompressionMethod`, and
`tuple[CompressionMethod, ...]` for filter chains:

```python
class CompressionAlgorithm(Enum):
    STORED = "stored"
    DEFLATE = "deflate"
    DEFLATE64 = "deflate64"
    BZIP2 = "bzip2"
    LZMA = "lzma"
    LZMA2 = "lzma2"
    ZSTD = "zstd"
    LZ4 = "lz4"
    BROTLI = "brotli"
    PPMD = "ppmd"
    BCJ = "bcj"
    BCJ2 = "bcj2"
    DELTA = "delta"
    UNKNOWN = "unknown"

@dataclass(frozen=True)
class CompressionMethod:
    algo: CompressionAlgorithm
    level: int | None = None
    properties: bytes | None = None
```

The enum SHALL be open-ended by appending new members over time. Unrecognized
codec IDs SHALL map to `UNKNOWN` rather than raising.

#### Scenario: compression matrix

| Case | Expected |
| --- | --- |
| ZIP member stored with DEFLATE | `(CompressionMethod(algo=CompressionAlgorithm.DEFLATE),)` |
| 7z member uses BCJ2 + LZMA2 | `(CompressionMethod(BCJ2), CompressionMethod(LZMA2))` |
| Archive contains an unknown codec ID | `CompressionAlgorithm.UNKNOWN`; no exception |

### Requirement: ArchiveMember exposes the complete mutable member record

The system SHALL define `ArchiveMember` as a mutable, unhashable dataclass that
callers treat as read-only:

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

Unavailable values SHALL be `None`; `name` follows normalization while
`raw_name` preserves stored bytes; timestamp timezone semantics are preserved;
digest keys name their real algorithms; there is no `crc32` alias. Sizes, link
targets, hashes, and diagnostics MAY be completed in place during streaming.
`member_id` / `archive_id` preserve source identity, convenience properties are
derived, and `replace()` creates an edited copy. `hashes`, `diagnostics`, and
`extra` SHALL be excluded from equality.

`ArchiveMember` SHALL remain unhashable and non-frozen. The `diagnostics` tuple
itself is immutable, but the library MAY replace it in place for later
member-specific events; previously returned members are live objects, not
point-in-time snapshots.

#### Scenario: member record matrix

| Case | Expected |
| --- | --- |
| Format cannot provide a field | Field is `None`, not a default |
| Streaming later learns `size` / `link_target` | Same yielded object is updated in place |
| Caller needs a renamed member | Uses `member.replace(name=...)`; original unchanged |
| `ArchiveMember` used as set item/dict key | Fails because the type is unhashable |
| Naive and aware `modified` values pass through `modified_utc()` | Returned values are aware UTC; original `modified` fields unchanged |
| ZIP CRC32 and RAR5 Blake2sp hashes | Stored under `"crc32"` int and `"blake2sp"` bytes keys respectively |
| Extraction report holds a member later completed in place | Report's result tuple is immutable; member object reflects late field update |

### Requirement: Member diagnostics attach under the shared budget

The system SHALL attach only retained, member-specific diagnostics to
`ArchiveMember.diagnostics` under the collector budget described by `diagnostics`.
Eligible initial codes are `MEMBER_NAME_NORMALIZED`,
`MEMBER_TIMESTAMP_INVALID`, `SYMLINK_TARGET_UNAVAILABLE`, and
`DIGEST_UNVERIFIABLE`. Attachment SHALL require both retained aggregate
occurrence and an additional attachment slot; aggregate and member copies share
the same occurrence id by value but not object identity.

`member.replace()` SHALL copy the tuple's current value without consuming another
library-retention slot. `ArchiveInfo` MUST NOT carry runtime diagnostics; those
live on `FormatInfo`, reader summaries, stream summaries, or extraction reports.

#### Scenario: member diagnostic matrix

| Case | Expected |
| --- | --- |
| Normalization emits retained member diagnostic and attachment slot remains | `member.diagnostics` exposes it with aggregate occurrence id |
| One collector budget slot remains | Aggregate retains; member attachment omitted; exact counts include event |
| Already-yielded stream discovers `DIGEST_UNVERIFIABLE` | Diagnostic may append in place, budget permitting |
| Rewind or missing EOF marker occurs after open | Reader/stream summary changes; frozen `ArchiveInfo` remains unchanged |

### Requirement: ArchiveMember name normalization is meaning-preserving only

The system SHALL normalize `ArchiveMember.name` using only meaning-preserving
rules and SHALL preserve verbatim stored bytes in `raw_name`. It MUST NOT strip a
leading `/` or collapse `..` components, because those rewrites hide unsafe
stored names. Such names are rejected by `safe-extraction`, not re-rooted at read
time. When normalization changes the presented path, Archivey SHALL emit the
documented normalization diagnostic/log event.

Normalization SHALL apply in order:

1. Replace `\` with `/` only when the backend says backslash is a separator
   (RAR; ZIP from DOS/Windows create systems). TAR/POSIX keeps `\` literal.
2. Strip leading `./` and collapse interior `/./`.
3. Collapse repeated `//`.
4. Append `/` for directory members.
5. Never produce an empty string; empty/root becomes `"."`.

#### Scenario: normalization matrix

| Case | Expected |
| --- | --- |
| Windows-origin bytes `b"foo\\bar\\baz.txt"` | `name == "foo/bar/baz.txt"`; `raw_name` unchanged |
| TAR bytes `b"weird\\name.txt"` | Backslash preserved as a literal |
| `"foo/../bar"` | `..` retained; not collapsed |
| `"/etc/passwd"` | Leading `/` retained; extraction rejects later |
| `"../../etc/passwd"` | Traversal retained; extraction rejects later |
| `"a//b/./c"` | `name == "a/b/c"` |
| Directory stored as `"mydir"` | `name == "mydir/"` |

### Requirement: ArchiveInfo carries open-time archive metadata

The system SHALL define frozen `ArchiveInfo` for metadata available immediately
after `open_archive()` without a full member scan:

```python
@dataclass(frozen=True)
class ArchiveInfo:
    format: ArchiveFormat
    format_version: str | None
    is_solid: bool
    member_count: int | None
    comment: str | None
    is_encrypted: bool
    is_multivolume: bool
    cost: CostReceipt
    extra: dict[str, Any] = field(default_factory=dict, compare=False)
```

`extra` keys SHALL be namespaced strings and excluded from equality.
`member_count` SHALL be `None` when computing it requires a full scan.

#### Scenario: archive info matrix

| Case | Expected |
| --- | --- |
| TAR archive opens without central directory | `ar.info.member_count is None` |
| ISO 9660 image richest namespace is Joliet | `ar.info.extra["iso.namespace"] == "joliet"` |
