# archive-data-model — member diagnostic attachment and complete schema

## MODIFIED Requirements

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
