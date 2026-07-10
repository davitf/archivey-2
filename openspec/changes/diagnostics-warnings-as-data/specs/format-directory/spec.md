# format-directory — scan-race diagnostics

## MODIFIED Requirements

### Requirement: Scan errors are loud, races are tolerated

Genuine directory-walk `OSError`s SHALL continue to propagate unchanged. When a listed
entry or subdirectory vanishes before inspection, the reader SHALL continue and emit
`SCAN_ENTRY_VANISHED` or `SCAN_DIRECTORY_VANISHED` with a JSON-safe relative path and path
kind. These events are reader-operation aggregate data and SHALL not attach to a member
that does not exist.

Under `RAISE`, `DiagnosticRaisedError` SHALL halt the scan. Context SHALL not retain
`DirEntry`, `Path`, exception, or filesystem handle objects.

#### Scenario: entry deleted mid-walk is collected

- **WHEN** an entry disappears between directory listing and `stat` under default policy
- **THEN** it is skipped, `SCAN_ENTRY_VANISHED` is counted/retained/logged on the reader, and the walk continues

#### Scenario: directory race is escalated by policy

- **WHEN** a subdirectory vanishes and `SCAN_DIRECTORY_VANISHED` resolves to `RAISE`
- **THEN** `DiagnosticRaisedError` halts the scan

#### Scenario: permission error remains genuine I/O

- **WHEN** walking a subdirectory raises `PermissionError`
- **THEN** that original error propagates unchanged and no vanished-path diagnostic substitutes for it
