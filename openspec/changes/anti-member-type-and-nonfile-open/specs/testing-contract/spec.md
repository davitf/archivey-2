## ADDED Requirements

### Requirement: Non-file open/read and ANTI classification are tested cross-format

The system SHALL test that `open()` / `read()` raise `ArchiveyUsageError` for directory
members on every backend that can produce them (ZIP, TAR, ISO, directory, and 7z when
present), and that `stream_members` yields `None` for those members. When 7z anti-item
fixtures are available, tests SHALL assert `type == MemberType.ANTI`, no stream in
`stream_members`, and `ArchiveyUsageError` from `open()`/`read()`.

#### Scenario: ZIP directory open raises

- **WHEN** a ZIP archive with a directory member is opened and `ar.read(dir_member)` is called
- **THEN** `ArchiveyUsageError` is raised

#### Scenario: directory-reader directory open raises ArchiveyUsageError

- **WHEN** the directory backend's `ar.open(subdir_member)` is called
- **THEN** `ArchiveyUsageError` is raised (not a raw `IsADirectoryError`)

#### Scenario: 7z anti-item contract

- **WHEN** a 7z archive containing an anti-item is listed and opened
- **THEN** the anti member has `type == MemberType.ANTI`, `stream_members` pairs it with `None`, and `open`/`read` raise `ArchiveyUsageError`
