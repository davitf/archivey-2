## ADDED Requirements

### Requirement: ZIP directory members are non-file for open/read

ZIP directory members SHALL follow the `archive-reading` non-file rule: no empty
payload stream from `open()` / `read()`; those calls raise `ArchiveyUsageError`.

#### Scenario: opening a ZIP directory raises

- **WHEN** `ar.open(zip_directory_member)` is called
- **THEN** `ArchiveyUsageError` is raised
