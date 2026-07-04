# format-tar — Phase 5 deltas

## MODIFIED Requirements

### Requirement: Detect truncated TAR archives

The system SHALL verify archive integrity at the end of iteration by checking for valid end-of-archive markers.

After iterating all members, the system verifies that the final 512-byte block(s) are null-filled end-of-archive markers. Strictness is configured by `ArchiveyConfig.strict_archive_eof` (see `archive-reading`; the Phase 4 `open_archive(strict_eof=)` keyword is removed). If the markers are absent:

- By default (`config.strict_archive_eof=False`): emit a `logging.WARNING` via the `archivey.backends.*` logger.
- When `config.strict_archive_eof=True`: raise `TruncatedError`.

#### Scenario: Valid TAR end-of-archive markers present

- **WHEN** all TAR members have been iterated
- **AND** the archive ends with null-filled 512-byte end-of-archive block(s)
- **THEN** no warning or error is emitted

#### Scenario: Missing end-of-archive markers, default mode

- **WHEN** all TAR members have been iterated
- **AND** the archive does not end with valid null-filled end-of-archive block(s)
- **AND** `config.strict_archive_eof` is `False` (the default)
- **THEN** the system emits a `logging.WARNING` indicating the archive may be truncated

#### Scenario: Missing end-of-archive markers, strict mode

- **WHEN** all TAR members have been iterated
- **AND** the archive does not end with valid null-filled end-of-archive block(s)
- **AND** `config.strict_archive_eof` is `True`
- **THEN** the system raises `TruncatedError`
