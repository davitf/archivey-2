## ADDED Requirements

### Requirement: ArchiveMember anti-item flag

The system SHALL expose `is_anti: bool = False` on `ArchiveMember`. When `True`, the
member is a format-level deletion marker (7z ANTI), not ordinary file content.
Callers iterating the archive SHALL see anti members unless a future optional filter
excludes them. Equality includes `is_anti`.

#### Scenario: default is non-anti

- **WHEN** a member is produced for a normal file, directory, or link
- **THEN** `member.is_anti` is `False`

#### Scenario: 7z anti-item sets the flag

- **WHEN** a 7z anti-item is parsed
- **THEN** `member.is_anti` is `True`
