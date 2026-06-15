# Error Handling

## Purpose

Error handling provides a single rooted exception hierarchy so callers can catch all library errors with one `except ArchiveyError` clause, while still distinguishing specific failure modes. Every error carries a standard set of attributes and preserves the original cause and traceback so that no diagnostic information is silently discarded.

## Requirements

### Requirement: Single Rooted Exception Hierarchy

The system SHALL define all library exceptions as subclasses of `ArchiveyError`, which itself subclasses the built-in `Exception`. The full hierarchy is:

```
ArchiveyError(Exception)
├── OpenError                   # cannot open / parse the archive header
│   ├── FormatDetectionError    # could not detect format
│   └── UnsupportedFormatError  # format detected but no backend available
├── ReadError                   # error reading a member
│   ├── CorruptionError         # CRC mismatch, bad data block
│   ├── TruncatedError          # unexpected EOF
│   └── EncryptionError         # password required or wrong password
├── WriteError                  # error writing an archive
├── ExtractionError             # error extracting a member to disk
│   └── FilterRejectionError    # safety filter blocked the member
│       ├── PathTraversalError  # ../ or absolute path
│       ├── SymlinkEscapeError  # symlink resolves outside dest
│       └── SpecialFileError    # device node, FIFO, socket
├── UnsupportedFeatureError     # recognized but unhandled feature/variant/codec
│                               #   (e.g. multi-volume, RAR2, BCJ2, unknown coder)
├── PackageNotInstalledError    # a required optional package or external tool is
│                               #   absent (codec backend, crypto backend, unrar)
└── UnsupportedOperationError   # e.g. random access on sequential reader
```

`UnsupportedFeatureError` and `PackageNotInstalledError` may be raised at open or
read time (e.g. listing a multi-volume archive, or reading a member whose codec
package is missing), so they are top-level `ArchiveyError` subtypes rather than
nested under `OpenError`/`ReadError`.

#### Scenario: catch all library errors with a single clause

- **WHEN** any operation (open, read, extract, write) fails due to a library-detected error
- **THEN** the raised exception is an instance of `ArchiveyError` (or a subclass), so `except ArchiveyError` catches it

#### Scenario: distinguish specific error subtypes

- **WHEN** an archive member has a bad CRC
- **THEN** `CorruptionError` is raised, allowing callers to handle it separately from, for example, `EncryptionError`

#### Scenario: missing optional package or tool

- **WHEN** a member requires a codec, crypto backend, or external tool whose package/binary is not installed (e.g. `pyppmd`, the crypto backend, or `unrar`)
- **THEN** `PackageNotInstalledError` is raised, naming the missing package or tool

#### Scenario: recognized but unsupported feature

- **WHEN** an archive uses a recognized feature the reader does not handle (e.g. multi-volume, RAR2, or the BCJ2 coder)
- **THEN** `UnsupportedFeatureError` is raised rather than producing incorrect output

---

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
