## ADDED Requirements

### Requirement: Directory members are non-file for open/read

Directory members exposed by the directory backend SHALL follow the `archive-reading`
non-file rule: `stream_members` yields `None`, and `open()` / `read()` raise
`ArchiveyUsageError` rather than propagating a raw `IsADirectoryError`.

#### Scenario: opening a subdirectory member raises usage error

- **WHEN** `ar.open(subdir_member)` is called on a directory archive
- **THEN** `ArchiveyUsageError` is raised
