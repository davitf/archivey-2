## MODIFIED Requirements

### Requirement: ArchiveMember type taxonomy (MemberType)

The system SHALL define a `MemberType` enum describing the kind of filesystem object each archive member represents.

```python
class MemberType(Enum):
    FILE      = "file"
    DIRECTORY = "directory"
    SYMLINK   = "symlink"       # includes Windows junction (flagged via extra["is_junction"])
    HARDLINK  = "hardlink"
    OTHER     = "other"         # device nodes, FIFOs, sockets — extraction always rejected
    ANTI      = "anti"          # deletion / tombstone marker (e.g. 7z ANTI bit)
```

Windows NTFS junction points SHALL be surfaced as `MemberType.SYMLINK` with `extra["is_junction"] = True`. Members of type `OTHER` SHALL always be rejected during extraction regardless of policy. Members of type `ANTI` SHALL NOT be treated as `OTHER`: they are extractable deletion markers under `safe-extraction`, have `is_file == False`, and carry no payload.

#### Scenario: device node is classified as OTHER

- **WHEN** a TAR archive contains a device node or FIFO
- **THEN** the corresponding `ArchiveMember` has `type == MemberType.OTHER`

#### Scenario: Windows junction surfaced as SYMLINK

- **WHEN** a ZIP archive contains a Windows junction point
- **THEN** the corresponding `ArchiveMember` has `type == MemberType.SYMLINK` and `extra["is_junction"] == True`

#### Scenario: 7z anti-item is classified as ANTI

- **WHEN** a 7z archive entry has the ANTI bit set
- **THEN** the corresponding `ArchiveMember` has `type == MemberType.ANTI`, `is_anti is True`, and `is_file is False`

## ADDED Requirements

### Requirement: ArchiveMember.is_anti convenience property

The system SHALL expose `ArchiveMember.is_anti` as a read-only property equivalent to
`type == MemberType.ANTI` (same shape as `is_file` / `is_dir` / `is_other`). There is no
separate `is_anti` dataclass field.

#### Scenario: default non-anti member

- **WHEN** an `ArchiveMember` has `type == MemberType.FILE` (or DIRECTORY / SYMLINK / HARDLINK / OTHER)
- **THEN** `member.is_anti` is `False`

#### Scenario: anti type sets the property

- **WHEN** an `ArchiveMember` has `type == MemberType.ANTI`
- **THEN** `member.is_anti` is `True`
