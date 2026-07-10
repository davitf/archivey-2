# error-handling — diagnostic escalation

## MODIFIED Requirements

### Requirement: Single Rooted Exception Hierarchy

The system SHALL define every library exception under this complete hierarchy:

```text
ArchiveyError(Exception)
├── OpenError
│   ├── FormatDetectionError
│   ├── UnsupportedFormatError
│   └── StreamNotSeekableError
├── ReadError
│   ├── CorruptionError
│   ├── TruncatedError
│   ├── EncryptionError
│   └── LinkTargetNotFoundError
├── WriteError
├── ExtractionError
│   └── FilterRejectionError
│       ├── PathTraversalError
│       ├── SymlinkEscapeError
│       └── SpecialFileError
├── UnsupportedFeatureError
├── PackageNotInstalledError
├── UnsupportedOperationError
└── DiagnosticRaisedError
```

All existing meanings and subclass boundaries remain. In particular,
`UnsupportedFeatureError`/`PackageNotInstalledError` may occur at open or read time,
`StreamNotSeekableError` is an `OpenError`, and `UnsupportedOperationError` denotes API
misuse or invalid reader mode. `DiagnosticRaisedError` is a direct `ArchiveyError`
subclass because diagnostic escalation can occur in detection, open, read, stream, or
extraction and is not itself one of those underlying failures.

#### Scenario: catch escalation at the common root

- **WHEN** diagnostic policy escalates an advisory occurrence
- **THEN** `except ArchiveyError` catches the resulting `DiagnosticRaisedError`

## ADDED Requirements

### Requirement: DiagnosticRaisedError is the typed escalation bridge

The public exception hierarchy SHALL add a direct `ArchiveyError` subtype:

```python
class DiagnosticRaisedError(ArchiveyError):
    diagnostic: Diagnostic
```

It SHALL require and expose the escalated immutable diagnostic. The standard
`source_format`, `archive_name`, and `member_name` fields SHALL be stamped through the
existing central context mechanism. Escalation alone has no underlying exception, so
`__cause__` MAY be `None`; an exception from logging/callback delivery propagates itself
instead and is not replaced.

`DiagnosticRaisedError` is an always-stop control exception. Extraction SHALL propagate
it even under `OnError.CONTINUE`, never record it as `FAILED`/`REJECTED`, and never proceed
to another member.

#### Scenario: strict policy raises a typed error carrying data

- **WHEN** a code resolves to `RAISE` and logging/callback delivery returns normally
- **THEN** `DiagnosticRaisedError` is raised with the exact emitted diagnostic and centrally stamped archive/member context

#### Scenario: extraction continuation cannot swallow escalation

- **WHEN** a member diagnostic escalates during `OnError.CONTINUE`
- **THEN** `DiagnosticRaisedError` propagates immediately and extraction halts

### Requirement: Specialized archive EOF strictness takes precedence

For `ARCHIVE_EOF_MARKER_MISSING`, `ArchiveyConfig.strict_archive_eof=True` SHALL force
`TruncatedError` after the diagnostic's policy-controlled count/retention/log/callback
steps. This specific validation error SHALL take precedence over
`DiagnosticRaisedError`: even when the code resolves to `RAISE`, the terminal exception is
`TruncatedError`. With strict EOF disabled, the normal disposition applies.

A logging-handler or callback exception still propagates at its earlier ordered delivery
step and therefore prevents either terminal exception.

#### Scenario: strict EOF overrides ignored disposition

- **WHEN** the EOF code resolves to `IGNORE` but `strict_archive_eof=True`
- **THEN** the exact diagnostic count increments and `TruncatedError` is raised without retention/logging/callback delivery

#### Scenario: strict EOF overrides diagnostic escalation type

- **WHEN** the EOF code resolves to `RAISE`, delivery succeeds, and `strict_archive_eof=True`
- **THEN** the event is retained/logged/called back according to `RAISE`, then `TruncatedError` is raised instead of `DiagnosticRaisedError`

#### Scenario: non-strict EOF follows ordinary raise policy

- **WHEN** the EOF code resolves to `RAISE` and `strict_archive_eof=False`
- **THEN** `DiagnosticRaisedError` is raised after delivery
