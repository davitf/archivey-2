## ADDED Requirements

### Requirement: 7z anti-items use MemberType.ANTI

The system SHALL expose every 7z `FILES_INFO` ANTI-bit entry as an `ArchiveMember` with
`type == MemberType.ANTI` (hence `is_anti is True` and `is_file is False`). Anti-items
SHALL appear in the member list and during iteration. They SHALL NOT be classified as
`MemberType.FILE` or `MemberType.OTHER`.

`open()` / `read()` of an anti-item SHALL raise `ArchiveyUsageError` per `archive-reading`
(no empty payload stream). `stream_members` SHALL yield `None` as the stream.
Extraction behavior remains as defined by `safe-extraction` for anti-items.

This supersedes any earlier “opening an anti-item yields no payload” scenario from
`native-7z-reader`.

#### Scenario: anti-item type is ANTI

- **WHEN** a 7z archive contains an anti-item
- **THEN** that member has `type == MemberType.ANTI` and `is_file is False`

#### Scenario: opening an anti-item raises

- **WHEN** `ar.open(anti_member)` or `ar.read(anti_member)` is called
- **THEN** `ArchiveyUsageError` is raised

#### Scenario: stream_members yields no stream for anti-items

- **WHEN** `stream_members` yields an anti-item
- **THEN** the paired stream is `None`
