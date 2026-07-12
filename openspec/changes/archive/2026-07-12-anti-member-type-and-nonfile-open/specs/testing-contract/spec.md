## ADDED Requirements

### Requirement: Non-file open and ANTI classification tests

Tests SHALL assert `ArchiveyUsageError` from `open`/`read` on directory members
for ZIP, TAR, ISO, and the directory backend (not empty bytes / raw OS /
ISO `CorruptionError`), and `stream_members` stream `None`. 7z anti fixtures
SHALL assert `type == MemberType.ANTI`, `None` stream, and usage-error open/read.

#### Scenario: coverage matrix

| Case | Expected |
| --- | --- |
| ZIP/TAR/ISO/directory dir member `read` | `ArchiveyUsageError` |
| Directory backend dir `open` | `ArchiveyUsageError` (not `IsADirectoryError`) |
| 7z anti list + stream + open | `ANTI`; stream `None`; `ArchiveyUsageError` |
