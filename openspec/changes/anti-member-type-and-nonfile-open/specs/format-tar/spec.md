## ADDED Requirements

### Requirement: TAR directory members are non-file for open/read

TAR directory members SHALL follow the `archive-reading` non-file rule: no empty
payload stream from `open()` / `read()`; those calls raise `ArchiveyUsageError`.

#### Scenario: opening a TAR directory raises

- **WHEN** `ar.open(tar_directory_member)` is called
- **THEN** `ArchiveyUsageError` is raised
