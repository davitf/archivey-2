# Error Handling

## Purpose

Error handling provides a single rooted exception hierarchy so callers can catch all library errors with one `except ArchiveyError` clause, while still distinguishing specific failure modes. Every error carries a standard set of attributes and preserves the original cause and traceback so that no diagnostic information is silently discarded.

## Requirements

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

**`UnsupportedOperationError` vs `UnsupportedFeatureError` — a deliberate split:**

- `UnsupportedOperationError` signals **API misuse**: the caller asked for something this
  reader's *mode* does not permit — random access (`get`, materialization)
  on a `streaming=True` reader, writing through a read-only RAR backend, or using a
  closed reader. It is not caused by the archive's contents; choosing a different
  access mode/usage avoids it. It can therefore occur in normal use when the wrong access mode
  was selected, but the fix is always on the caller's side.
- `UnsupportedFeatureError` signals a **valid archive with a feature Archivey does not
  implement** (a ZIP codec stdlib `zipfile` lacks, an AES-encrypted ZIP entry, the 7z
  BCJ2 coder, an unknown coder): nothing the caller does changes it; the archive itself
  needs an unsupported capability.

#### Scenario: catch all library errors with a single clause

- **WHEN** any operation (open, read, extract, write) fails due to a library-detected error
- **THEN** the raised exception is an instance of `ArchiveyError` (or a subclass), so `except ArchiveyError` catches it

#### Scenario: catch escalation at the common root

- **WHEN** diagnostic policy escalates an advisory occurrence
- **THEN** `except ArchiveyError` catches the resulting `DiagnosticRaisedError`

#### Scenario: distinguish specific error subtypes

- **WHEN** an archive member has a bad CRC
- **THEN** `CorruptionError` is raised, allowing callers to handle it separately from, for example, `EncryptionError`

#### Scenario: missing optional package or tool

- **WHEN** a member requires a codec, crypto backend, or external tool whose package/binary is not installed (e.g. `pyppmd`, the crypto backend, or `unrar`)
- **THEN** `PackageNotInstalledError` is raised, naming the missing package or tool

#### Scenario: recognized but unsupported feature

- **WHEN** an archive uses a recognized feature the reader does not handle (e.g. a ZIP compression method stdlib `zipfile` doesn't implement, or the 7z BCJ2 coder)
- **THEN** `UnsupportedFeatureError` is raised rather than producing incorrect output

### Requirement: Usage errors are a separate hierarchy from ArchiveyError

The system SHALL define `ArchiveyUsageError(Exception)` — deliberately **not** an
`ArchiveyError` subclass — as the root for errors that indicate a bug in the calling
code rather than a property of the archive, the environment, or a supported limitation.
`except ArchiveyError` is what applications wrap archive handling in; it MUST NOT
swallow caller misuse.

`ConcurrentAccessError(ArchiveyUsageError)` SHALL be raised when a second member stream
is opened while another is live on a reader without `MemberStreams.CONCURRENT`. Its
message SHALL include the recorded `open_archive()` call site (`file:line`) so the error
points at where the capability should have been declared.

`ArchiveyUsageError` (the root, or a future subclass) SHALL cover the other detected
misuse states:

- a reader-wide single-owner pass overlapping another distinct pass (`__iter__` /
  `stream_members` / `extract_all`) or overlapping active worker calls;
- without `MemberStreams.CONCURRENT`, a reader close overlapping an actively executing
  member-worker call (an idle leased stream is not overlap; under `CONCURRENT`, close
  drains workers instead);
- any new reader operation/property except repeated `close()` / `__exit__` after
  `reader.close()`;
- same-reader password-provider reentry into a password-requiring operation that would
  deadlock;
- opening/using an `ArchiveMember` that does not belong to this reader (wrong-reader
  identity); and
- member I/O after the caller closed its own supplied source early.

The error SHALL be raised at the later operation before it changes state and MUST leave
the earlier operation/stream usable.

`open_archive(streaming=True, member_streams=…CONCURRENT…)` SHALL raise
`ArchiveyUsageError` (invalid access-mode/capability combination).

Boundaries of the hierarchy:

- `UnsupportedOperationError` and `UnsupportedFeatureError` remain `ArchiveyError`s:
  they describe what an archive, format, backend, or access mode cannot provide — an
  input/environment property, not a caller bug.
- Stream-level conventions stay stdlib-shaped and are not archivey taxonomy: I/O on a
  **closed stream** raises `ValueError`; unsupported positioning raises
  `io.UnsupportedOperation` (this is also how undeclared `SEEKABLE` surfaces, because
  seek-probing consumers already check `seekable()`/catch that type).

Internal operation-owner children are not overlap: materialization/worker link reads,
`extract_all()` member/counter peeks and owned `stream_members()` passes, and I/O/close
on a pass's yielded stream carry the root token explicitly. Reentrant public calls do
not inherit that token implicitly and remain rejectable.

#### Scenario: usage errors are not caught by ArchiveyError handlers

- **WHEN** application code wraps archive handling in `except ArchiveyError` and a
  `ConcurrentAccessError` or other `ArchiveyUsageError` is raised
- **THEN** the usage error propagates past that handler, surfacing the calling-code bug
  instead of being treated as an archive problem

#### Scenario: undeclared concurrent open names the open site

- **WHEN** a second overlapping member stream is opened on a reader without
  `MemberStreams.CONCURRENT`
- **THEN** `ConcurrentAccessError` is raised with the `open_archive()` caller's
  `file:line` in its message, and the first stream is not invalidated

#### Scenario: detected unsupported overlap is a usage error

- **WHEN** an exclusive reader pass/materialization operation is active and a conflicting
  operation begins
- **THEN** the later operation raises `ArchiveyUsageError` and the active operation
  is not invalidated

#### Scenario: post-close reader operation or property is rejected

- **WHEN** any reader method/property other than idempotent `close()` / `__exit__` is used
  after `reader.close()`
- **THEN** `ArchiveyUsageError` is raised
- **AND** an already-open member stream remains governed by the lifecycle-lease contract

#### Scenario: repeated close remains idempotent

- **WHEN** `reader.close()` is called after the reader is already closed
- **THEN** it returns without error or repeated backend teardown

#### Scenario: unsupported positioning uses the standard stream exception

- **WHEN** `seek()` is called on a member stream without declared `SEEKABLE`, or on a
  stream whose backend cannot position
- **THEN** normal `io.UnsupportedOperation` behavior applies, not an archivey-typed error

#### Scenario: teardown error propagates once after state closes

- **WHEN** explicit reader/member close performs final backend teardown and it fails
- **THEN** the translated close error propagates after state becomes irrevocably closed
- **AND** repeated close does not retry or re-raise the teardown

#### Scenario: simultaneous close failures are grouped

- **WHEN** final member close has both an inner-stream close failure and backend teardown
  failure
- **THEN** both translated errors are preserved in an `ExceptionGroup` after state/leases are
  irrevocably released

#### Scenario: caller-owned source closed too early fails as usage error

- **WHEN** a caller closes its supplied `BinaryIO` before an escaped member stream is done
- **THEN** later member I/O raises `ArchiveyUsageError` for the closed source rather
  than returning arbitrary/empty bytes

#### Scenario: declared simultaneous random member streams are not errors

- **WHEN** workers open and operate on independent member streams after materialization
  on a reader declared with `MemberStreams.CONCURRENT`
- **THEN** no concurrency exception is raised

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

### Requirement: Required Attributes on Every ArchiveyError

The system SHALL ensure that every instance of `ArchiveyError` (and any subclass) carries the following attributes:

| Attribute | Type | Description |
|---|---|---|
| `message` | `str` | Human-readable explanation of the error |
| `source_format` | `ArchiveFormat \| None` | The archive format being processed, if known |
| `archive_name` | `str \| None` | A name identifying the archive (its path, or a name carried by the source stream such as `BinaryIO.name`), for clearer error messages. `None` when the source is an anonymous stream that carries no name |
| `member_name` | `str \| None` | The name of the member being processed at the time of the error, if applicable |
| `__cause__` | `BaseException \| None` | The original exception, preserved via `raise ... from exc` |

`archive_name` is best-effort: it is populated from the path when the archive was
opened from a path, or from the stream's `name` attribute when present, and is
otherwise `None` — the library never fabricates one.

#### Scenario: error carries format and member name

- **WHEN** a `CorruptionError` is raised while reading member `"data/file.txt"` from a ZIP archive
- **THEN** the exception has `source_format == ArchiveFormat.ZIP` and `member_name == "data/file.txt"`

#### Scenario: error without a specific member context

- **WHEN** a `FormatDetectionError` is raised before any member is accessed
- **THEN** the exception has `member_name == None`

---

### Requirement: Original Cause and Traceback Must Be Preserved

The system SHALL preserve the original underlying exception as `__cause__` on every `ArchiveyError` using `raise ArchiveyError(...) from original_exc`. Libraries MUST NOT swallow the original exception. The original traceback MUST be attached and visible through a standard `traceback.print_exc()` call.

Exception translation has two separable concerns; neither is scattered as manual
field-setting across the backends.

1. **Type translation is per-library, not per-format.** Each underlying library has
   its own exception taxonomy (`zipfile.BadZipFile`, `tarfile.TarError`,
   `lzma.LZMAError`, the `unrar` process errors, the crypto backend's errors, …), so
   a small translator *per library* maps those exceptions to the correct
   `ArchiveyError` subclass (`CorruptionError`, `TruncatedError`, `EncryptionError`,
   …) by inspecting the exception type/payload. A library shared across formats
   (e.g. `lzma`, used by both XZ and 7z) is translated once and reused. Translators
   know nothing about the archive format, path, or member.

   ```python
   # Maps one library's exceptions to typed ArchiveyErrors; no format/context.
   @translate_library_errors(LZMA_TRANSLATOR)
   def _read_block(self, ...): ...
   ```

2. **Context is stamped centrally.** The `ArchiveReader` ABC wraps the public
   operations (listing, `open()`, `read()`, iteration, extraction) so that when an
   `ArchiveyError` propagates, the base class fills in `source_format`,
   `archive_name`, and `member_name` from the context it already holds, then
   re-raises. Backends therefore do **not** set these fields by hand, which avoids
   repetitive, error-prone code and keeps the per-library translators context-free.

   ```python
   # In the ArchiveReader ABC, around each public operation:
   try:
       return op()
   except ArchiveyError as exc:
       exc.source_format = exc.source_format or self.format
       exc.archive_name  = exc.archive_name  or self._archive_name
       exc.member_name   = exc.member_name   or current_member_name
       raise
   ```

No internal library exception (e.g. `zipfile.BadZipFile`, `tarfile.TarError`, the
7z/RAR backend errors) SHALL propagate to the caller unwrapped.

A backend that serves member bytes through **no** decoding library — the directory
backend, or a STORED/uncompressed member read as raw bytes — has no library exception
taxonomy to translate, so it returns the underlying stream directly rather than wrapping
it in a translator. This is deliberate, not a missing wrap: there is nothing to
translate, and a genuine `OSError` from such a read MUST propagate unchanged per *Genuine
runtime and I/O errors are not reclassified* below. The translate-and-stamp wrapper is
applied only by backends that drive a codec, where a raw decode error must become a typed
`ArchiveyError`.

#### Scenario: original exception attached as __cause__

- **WHEN** `zipfile.BadZipFile` is raised internally and wrapped as `CorruptionError`
- **THEN** the `CorruptionError.__cause__` is the original `zipfile.BadZipFile` instance

#### Scenario: original traceback visible in default output

- **WHEN** `traceback.print_exc()` is called after catching an `ArchiveyError`
- **THEN** the output includes the original underlying exception and its traceback (via the chained-exception display)

#### Scenario: no bare re-raise or exception swallowing

- **WHEN** a decoding library raises an unexpected exception inside any backend method
- **THEN** it is caught and re-raised as an `ArchiveyError` subclass using `raise ... from exc`; the original exception is never silently discarded

#### Scenario: context filled by the base reader, not the backend

- **WHEN** a per-library translator raises a `CorruptionError` with no `source_format`/`archive_name`/`member_name` set, while reading member `"data/file.txt"` of a 7z archive opened from `"/tmp/a.7z"`
- **THEN** the `ArchiveReader` ABC stamps `source_format == ArchiveFormat.SEVEN_Z`, `archive_name == "/tmp/a.7z"`, and `member_name == "data/file.txt"` before the error reaches the caller

---

### Requirement: Genuine runtime and I/O errors are not reclassified

The system SHALL translate only exceptions that originate from a decoding library's
own taxonomy (corrupt/truncated/encrypted data, unsupported coders, …). Failures
that are unrelated to archive decoding — an `OSError` from the filesystem, a dropped
network connection or other error from a caller-supplied stream, `KeyboardInterrupt`,
`MemoryError`, and similar — SHALL propagate **unchanged**, never reclassified as
`CorruptionError`/`TruncatedError` or any other `ArchiveyError`. The base reader MAY
stamp context onto an `ArchiveyError` it is already re-raising, but it MUST NOT
convert an unrelated runtime exception into an `ArchiveyError`.

#### Scenario: filesystem error propagates unwrapped

- **WHEN** a read from the underlying file fails with `OSError` (e.g. an I/O error or a disconnected network mount) partway through reading a member
- **THEN** the original `OSError` propagates to the caller unchanged, not wrapped as `CorruptionError` or `TruncatedError`

#### Scenario: a truncated/corrupt stream is still an archive error

- **WHEN** the source bytes are fully readable but the decoder reports corrupt or prematurely-ended compressed data
- **THEN** the error IS translated to `CorruptionError`/`TruncatedError`, because it originates from decoding rather than from the source's I/O
