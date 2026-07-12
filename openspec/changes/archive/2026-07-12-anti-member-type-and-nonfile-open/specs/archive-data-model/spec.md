## MODIFIED Requirements

### Requirement: MemberType describes filesystem object kind

The system SHALL define:

```python
class MemberType(Enum):
    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"
    HARDLINK = "hardlink"
    OTHER = "other"
    ANTI = "anti"
```

Windows NTFS junctions SHALL surface as `MemberType.SYMLINK` with
`extra["is_junction"] == True`. `MemberType.OTHER` SHALL always be rejected by
extraction regardless of policy. `MemberType.ANTI` SHALL be a deletion/tombstone
marker (`is_file` false, no payload); it SHALL NOT be treated as `OTHER`.

#### Scenario: member type matrix

| Case | Expected |
| --- | --- |
| TAR contains a device node or FIFO | `member.type == MemberType.OTHER` |
| ZIP contains a Windows junction | `member.type == MemberType.SYMLINK`; `member.extra["is_junction"] is True` |
| 7z ANTI-bit entry | `member.type == MemberType.ANTI`; `member.is_anti`; not `is_file` |

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
    is_current: bool = True
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
    def is_anti(self) -> bool: ...
    @property
    def is_junction(self) -> bool: ...

    def modified_utc(self, tz_for_naive: tzinfo | None = None) -> datetime | None: ...
    def replace(self, **kwargs: Any) -> "ArchiveMember": ...
```

`is_anti` SHALL be derived (`type == MemberType.ANTI`); there is no `is_anti` field.
`is_current` SHALL mean last-entry-wins live path state (7z computes it; others may
default `True`). Unavailable values SHALL be `None`; `name` follows normalization while
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
| `type == MemberType.ANTI` | `is_anti` true; `is_file` false |
| Content superseded by later same-name / anti | Earlier member `is_current` false |
