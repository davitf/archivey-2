## MODIFIED Requirements

### Requirement: Single rooted archive exception hierarchy

The system SHALL define every library-detected archive/environment failure under
this exact `ArchiveyError` hierarchy:

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
├── ResourceLimitError
├── UnsupportedFeatureError
├── PackageNotInstalledError
├── UnsupportedOperationError
└── DiagnosticRaisedError
```

Subclass boundaries SHALL keep their existing meanings:
`UnsupportedFeatureError` / `PackageNotInstalledError` may occur at open or read
time, `StreamNotSeekableError` is an `OpenError`, and
`UnsupportedOperationError` describes an archive/backend/access-mode operation
that cannot be provided, not a caller-code bug. `DiagnosticRaisedError` is direct
because advisory escalation can happen during detection, open, read, stream, or
extraction. `ResourceLimitError` is direct because configurable resource caps can
trip during listing materialization or extraction bomb guarding; it is not an
`ExtractionError` subclass.

| Error split | Meaning |
| --- | --- |
| `UnsupportedOperationError` | Valid API call against a reader/backend/mode that cannot provide the requested operation: random access on `streaming=True`, write through read-only RAR, operation on closed reader. |
| `UnsupportedFeatureError` | Valid archive uses a recognized feature Archivey does not implement: unsupported ZIP method, AES ZIP entry, 7z BCJ2, unknown coder. |
| `ResourceLimitError` | A configured listing or extraction resource limit was exceeded (`ListingLimits` / `ExtractionLimits` bomb guards). |

#### Scenario: archive exception matrix

| Case | Expected |
| --- | --- |
| Any open/read/extract/write failure detected by Archivey | Instance of `ArchiveyError`; `except ArchiveyError` catches it |
| Diagnostic policy escalates | `DiagnosticRaisedError` is caught by `except ArchiveyError` |
| Bad member CRC | `CorruptionError`, distinct from `EncryptionError` |
| Missing codec/package/tool such as `pyppmd`, crypto backend, or `unrar` | `PackageNotInstalledError` names the missing component |
| Recognized unsupported feature such as 7z BCJ2 | `UnsupportedFeatureError`; no incorrect output |
| Listing `max_members` exceeded | `ResourceLimitError`, not `ExtractionError` |
| Extraction `max_extracted_bytes` exceeded | `ResourceLimitError`, not `ExtractionError` |
