## ADDED Requirements

### Requirement: ISO directory members are non-file for open/read

ISO directory members SHALL follow the `archive-reading` non-file rule: `open()` /
`read()` raise `ArchiveyUsageError` rather than a format `CorruptionError` from the
underlying ISO library rejecting a directory path.

#### Scenario: opening an ISO directory raises usage error

- **WHEN** `ar.open(iso_directory_member)` is called
- **THEN** `ArchiveyUsageError` is raised (not `CorruptionError`)
