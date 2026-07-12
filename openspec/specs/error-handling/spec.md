# Error Handling

## Purpose

Archivey exposes one archive-error root for archive/environment failures while
keeping caller misuse outside that root. Errors preserve typed context, causes,
and tracebacks so callers can catch broadly, specialize narrowly, and still debug
the original failure.

## Related specs

| Spec | Relationship |
| --- | --- |
| `archive-reading` | Reader lifecycle, member streams, passwords, and close observables |
| `diagnostics` | Diagnostic value, delivery, callback, and retention rules |
| `access-mode-and-cost` | Access-mode legality and stream capability table |
| `reader-concurrency` | Ownership, overlap, worker, and teardown details |
| `compressed-streams` | Codec exception translation and digest verification |

## Requirements

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
extraction.

| Error split | Meaning |
| --- | --- |
| `UnsupportedOperationError` | Valid API call against a reader/backend/mode that cannot provide the requested operation: random access on `streaming=True`, write through read-only RAR, operation on closed reader. |
| `UnsupportedFeatureError` | Valid archive uses a recognized feature Archivey does not implement: unsupported ZIP method, AES ZIP entry, 7z BCJ2, unknown coder. |

#### Scenario: archive exception matrix

| Case | Expected |
| --- | --- |
| Any open/read/extract/write failure detected by Archivey | Instance of `ArchiveyError`; `except ArchiveyError` catches it |
| Diagnostic policy escalates | `DiagnosticRaisedError` is caught by `except ArchiveyError` |
| Bad member CRC | `CorruptionError`, distinct from `EncryptionError` |
| Missing codec/package/tool such as `pyppmd`, crypto backend, or `unrar` | `PackageNotInstalledError` names the missing component |
| Recognized unsupported feature such as 7z BCJ2 | `UnsupportedFeatureError`; no incorrect output |

### Requirement: Caller misuse remains outside ArchiveyError

The system SHALL define `ArchiveyUsageError(Exception)` outside `ArchiveyError`
for detected caller-code bugs. `except ArchiveyError` MUST NOT swallow misuse.

`ConcurrentAccessError(ArchiveyUsageError)` SHALL be raised when a second member
stream opens while another is live on a reader without `MemberStreams.CONCURRENT`.
Its message SHALL include the recorded `open_archive()` call site (`file:line`).

`ArchiveyUsageError` SHALL also cover:

- reader-wide exclusive pass overlap (`__iter__`, `stream_members`,
  `extract_all`, materialization, active worker calls);
- non-`CONCURRENT` close overlapping an active worker call;
- any reader operation/property after `close()` except repeated `close()` /
  `__exit__`;
- same-reader password-provider reentry into password-requiring work;
- using an `ArchiveMember` from another reader;
- member I/O after a caller closes its supplied source early;
- `open_archive(streaming=True, member_streams=...CONCURRENT...)`.

The later operation SHALL fail before changing state and MUST leave the earlier
operation/stream usable. Internal owner-child operations are exempt only through
explicit internal tokens; public reentry does not inherit them. Closed stream I/O
continues to raise `ValueError`, and unsupported stream positioning continues to
raise `io.UnsupportedOperation`.

#### Scenario: usage error matrix

| Case | Expected |
| --- | --- |
| `except ArchiveyError` wraps code that raises `ArchiveyUsageError` | Usage error propagates past the handler |
| Second overlapping member stream without `CONCURRENT` | `ConcurrentAccessError` with open-site `file:line`; first stream still readable |
| Exclusive pass/materialization is active and conflicting public op begins | Later op raises `ArchiveyUsageError`; active op remains valid |
| Operation/property after `reader.close()` | `ArchiveyUsageError`; already-open member stream follows lifecycle lease |
| Repeated `reader.close()` | No error; no repeated backend teardown |
| Unsupported `seek()` on a stream | `io.UnsupportedOperation`, not archivey-typed |
| Caller closes supplied `BinaryIO` before escaped stream finishes | Later member I/O raises `ArchiveyUsageError` |
| Declared `CONCURRENT`, post-materialization independent streams | No concurrency exception |

### Requirement: Close teardown failures preserve state and causes

The system SHALL make explicit reader/member close irrevocably close state before
propagating translated teardown errors. Repeated close MUST NOT retry or re-raise
the teardown. If final member close encounters both an inner-stream close failure
and backend teardown failure, both translated errors SHALL be preserved in an
`ExceptionGroup` after state and leases are released.

#### Scenario: teardown matrix

| Case | Expected |
| --- | --- |
| Explicit close teardown fails | Translated close error propagates after state becomes closed |
| Repeated close after teardown failure | No retry and no repeated error |
| Inner-stream close and backend teardown both fail | `ExceptionGroup` preserves both translated errors |

### Requirement: DiagnosticRaisedError is the typed escalation bridge

The system SHALL expose the escalated immutable diagnostic on a direct
`ArchiveyError` subtype:

```python
class DiagnosticRaisedError(ArchiveyError):
    diagnostic: Diagnostic
```

`source_format`, `archive_name`, and `member_name` SHALL be stamped through the
central context mechanism. Escalation alone has no underlying exception, so
`__cause__` may be `None`; logging/callback exceptions propagate themselves.
`DiagnosticRaisedError` MUST halt extraction even under `OnError.CONTINUE`.

#### Scenario: diagnostic escalation matrix

| Case | Expected |
| --- | --- |
| Code resolves to `RAISE` and delivery succeeds | `DiagnosticRaisedError` carries the exact emitted diagnostic plus stamped context |
| Member diagnostic escalates during `OnError.CONTINUE` | Error propagates immediately; extraction does not record `FAILED`/`REJECTED` or continue |

### Requirement: Archive EOF strictness takes precedence

For `ARCHIVE_EOF_MARKER_MISSING`, `ArchiveyConfig.strict_archive_eof=True`
SHALL force `TruncatedError` after the diagnostic policy-controlled
count/retention/log/callback steps. This terminal `TruncatedError` SHALL take
precedence over `DiagnosticRaisedError`; with strict EOF disabled, ordinary
diagnostic disposition applies. Logging-handler or callback exceptions still
propagate at their earlier delivery step.

#### Scenario: strict EOF matrix

| Case | Expected |
| --- | --- |
| EOF code resolves to `IGNORE`, `strict_archive_eof=True` | Exact count increments; `TruncatedError` raised without retention/logging/callback delivery |
| EOF code resolves to `RAISE`, delivery succeeds, strict EOF true | Retain/log/callback according to `RAISE`; raise `TruncatedError` instead of `DiagnosticRaisedError` |
| EOF code resolves to `RAISE`, strict EOF false | `DiagnosticRaisedError` after delivery |

### Requirement: Every ArchiveyError carries standard attributes

The system SHALL ensure every `ArchiveyError` instance carries:

| Attribute | Type | Contract |
| --- | --- | --- |
| `message` | `str` | Human-readable explanation |
| `source_format` | `ArchiveFormat | None` | Format being processed, if known |
| `archive_name` | `str | None` | Path or source stream `name`; `None` for anonymous streams; never fabricated |
| `member_name` | `str | None` | Member in context, if any |
| `__cause__` | `BaseException | None` | Original exception via `raise ... from exc` when wrapping |

#### Scenario: context attribute matrix

| Case | Expected |
| --- | --- |
| `CorruptionError` while reading ZIP member `"data/file.txt"` | `source_format == ArchiveFormat.ZIP`; `member_name == "data/file.txt"` |
| `FormatDetectionError` before any member | `member_name is None` |

### Requirement: Original cause and traceback are preserved centrally

The system SHALL preserve original decoding-library exceptions as `__cause__`
using `raise ... from exc`; libraries MUST NOT swallow the original traceback.
Type translation is per underlying library (for example `zipfile`, `tarfile`,
`lzma`, `unrar`, crypto backend), not per format. The `ArchiveReader` base class
SHALL centrally stamp `source_format`, `archive_name`, and `member_name` on
propagating `ArchiveyError`s; backends do not hand-fill those fields.

No internal library exception SHALL escape unwrapped when it originates from a
decoding library taxonomy. A backend that serves raw bytes through no decoding
library (directory backend, stored member stream) SHALL NOT wrap plain `OSError`;
there is no codec taxonomy to translate.

#### Scenario: translation matrix

| Case | Expected |
| --- | --- |
| `zipfile.BadZipFile` is wrapped as `CorruptionError` | `__cause__` is the original `BadZipFile` |
| `traceback.print_exc()` after catching `ArchiveyError` | Chained output includes original exception and traceback |
| Decoder raises an unexpected taxonomy exception | Re-raised as an `ArchiveyError` subclass with `raise ... from exc` |
| Context-free translator raises while reading 7z member `"data/file.txt"` from `"/tmp/a.7z"` | Base reader stamps `SEVEN_Z`, archive name, and member name |

### Requirement: Genuine runtime and I/O errors are not reclassified

The system SHALL translate only errors from a decoding library's archive/codec
taxonomy. Filesystem `OSError`, dropped caller-supplied streams, `KeyboardInterrupt`,
`MemoryError`, and similar runtime failures SHALL propagate unchanged and MUST NOT
be converted into `CorruptionError`, `TruncatedError`, or another `ArchiveyError`.

#### Scenario: runtime error matrix

| Case | Expected |
| --- | --- |
| Underlying file read raises `OSError` mid-member | Original `OSError` propagates unchanged |
| Source bytes are readable but decoder reports corrupt/truncated data | `CorruptionError` / `TruncatedError` because the failure is decoding-origin |
