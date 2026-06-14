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
| `member_name` | `str \| None` | The name of the member being processed at the time of the error, if applicable |
| `__cause__` | `BaseException \| None` | The original exception, preserved via `raise ... from exc` |

#### Scenario: error carries format and member name

- **WHEN** a `CorruptionError` is raised while reading member `"data/file.txt"` from a ZIP archive
- **THEN** the exception has `source_format == ArchiveFormat.ZIP` and `member_name == "data/file.txt"`

#### Scenario: error without a specific member context

- **WHEN** a `FormatDetectionError` is raised before any member is accessed
- **THEN** the exception has `member_name == None`

---

### Requirement: Original Cause and Traceback Must Be Preserved

The system SHALL preserve the original underlying exception as `__cause__` on every `ArchiveyError` using `raise ArchiveyError(...) from original_exc`. Libraries MUST NOT swallow the original exception. The original traceback MUST be attached and visible through a standard `traceback.print_exc()` call.

Every backend wraps its underlying library's exceptions at the call site using this pattern:

```python
try:
    raw_member = self._zf.getinfo(name)
except KeyError as exc:
    raise ReadError(f"Member '{name}' not found", format=ArchiveFormat.ZIP) from exc
except zipfile.BadZipFile as exc:
    raise CorruptionError("ZIP central directory corrupt", format=ArchiveFormat.ZIP) from exc
```

For the common case, a `@translate_errors(format)` decorator handles exception wrapping uniformly across backend methods:

```python
@translate_errors(ArchiveFormat.ZIP)
def open_member(self, member: Member) -> BinaryIO:
    return self._zf.open(member.original_name)
```

No internal library exception (e.g. `zipfile.BadZipFile`, `tarfile.TarError`, `py7zr` exceptions) SHALL propagate to the caller unwrapped.

#### Scenario: original exception attached as __cause__

- **WHEN** `zipfile.BadZipFile` is raised internally and wrapped as `CorruptionError`
- **THEN** the `CorruptionError.__cause__` is the original `zipfile.BadZipFile` instance

#### Scenario: original traceback visible in default output

- **WHEN** `traceback.print_exc()` is called after catching an `ArchiveyError`
- **THEN** the output includes the original underlying exception and its traceback (via the chained-exception display)

#### Scenario: no bare re-raise or exception swallowing

- **WHEN** an underlying library raises an unexpected exception inside any backend method
- **THEN** it is caught and re-raised as an `ArchiveyError` subclass using `raise ... from exc`; the original exception is never silently discarded
