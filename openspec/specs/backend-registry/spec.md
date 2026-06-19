# Backend Registry

## Purpose

The backend registry maps archive formats to the backend classes that can read or write them. Core backends are registered automatically at import time; optional backends register themselves only when their third-party dependency is available. The registry exposes a selection API used internally by `archivey.open_archive()`, `archivey.create()`, and `detect_format()`.

## Requirements

### Requirement: Backends self-register at import time

The system SHALL register core backends (ZIP, TAR, GZ/BZ2/XZ single-file compressors, Directory) automatically when `archivey` is imported, without any user action.

The system SHALL register library-backed optional backends (ISO, ZST, LZ4) and the optional 7-Zip *writing* capability inside a `try/except ImportError` guard. If the optional dependency is absent the guard catches the `ImportError` and the backend/capability is not registered; it does not raise at import time.

Note: 7-Zip and RAR *reading* are **native** and always registered (no import guard). RAR data reads additionally require the external `unrar` binary at runtime — a missing-tool condition handled at read time, not via an import guard (see `format-rar`).

```python
# formats/iso_reader.py
try:
    import pycdlib
    _PYCDLIB_AVAILABLE = True
except ImportError:
    _PYCDLIB_AVAILABLE = False

class IsoReadBackend(ReadBackend):
    FORMATS = (ArchiveFormat.ISO,)
    EXTENSIONS = (".iso",)
    MAGIC = ((32769, b"CD001"),)          # declared as data; the detector matches it
    OPTIONAL_DEPENDENCY = "pycdlib"
    def open_read(self, source, streaming, password, encoding, archive_name) -> ArchiveReader: ...

if _PYCDLIB_AVAILABLE:
    register_reader(IsoReadBackend)   # from archivey.internal.registry; only when usable
```

Read backends register via `register_reader(BackendClass)` and write backends via
`register_writer(BackendClass)` (from `archivey.internal.registry`). An optional backend is registered only
inside its successful-import guard, so an unavailable format never appears in
`list_formats()`/`list_writable_formats()`.

#### Scenario: core backend available without extras

- **WHEN** `import archivey` succeeds on a system with no optional extras installed
- **THEN** ZIP, TAR (all variants), GZ, BZ2, XZ, Directory, and the native 7z and RAR readers are registered and their formats appear in `BackendRegistry.list_formats()`

#### Scenario: optional backend absent at import

- **WHEN** `pycdlib` is not installed and `archivey` is imported
- **THEN** no `ImportError` is raised during import
- **AND** `ArchiveFormat.ISO` does not appear in `BackendRegistry.list_formats()`

---

### Requirement: Backend is a stateless factory

The system SHALL implement each `ReadBackend`/`WriteBackend` subclass as a stateless factory: the class holds no per-archive state. All archive state lives in the `ArchiveReader` or `ArchiveWriter` instance returned by `open_read()` or `open_write()`. This allows multiple readers to be open simultaneously from the same backend class and simplifies testing by making it possible to mock `ReadBackend.open_read()` to return a fake reader.

#### Scenario: two simultaneous readers from the same backend

- **WHEN** `archivey.open_archive("a.zip")` and `archivey.open_archive("b.zip")` are both open at the same time
- **THEN** each call returns an independent `ArchiveReader` instance with its own state
- **AND** operations on one reader do not affect the other

---

### Requirement: Detection owns matching; the registry selects a backend by format

The system SHALL keep **format detection** and **backend selection** as two separate
steps, rather than having each backend re-run byte matching:

1. `detect_format()` (the `format-detection` capability) is the single authority for
   *which format* a source is. It owns the central magic table and all special-case
   probes (SFX stub scan, inner-TAR probe, ISO extended window). The per-format magic it
   matches against is **declared as data** by each read backend (`MAGIC`/`MAGIC_OFFSET`/
   `EXTENSIONS`) and aggregated by the detector — backends do not each re-implement
   matching logic. Detection inspects bytes through the shared `PeekableStream` (it is
   passed the peekable/seekable source, not a fixed-size `bytes` snapshot, so probes that
   need a larger or decompressed window can request more), and consumes nothing.
2. The registry then maps the detected `ArchiveFormat` to a registered backend.

```python
class BackendRegistry:
    # read side
    def register_reader(self, backend_cls: type[ReadBackend]) -> None: ...
    def reader_for_format(self, format: ArchiveFormat) -> type[ReadBackend]: ...
    # write side (separate registry of write backends)
    def register_writer(self, backend_cls: type[WriteBackend]) -> None: ...
    def writer_for_format(self, format: ArchiveFormat) -> type[WriteBackend]: ...
    # availability
    def list_formats(self) -> list[ArchiveFormat]: ...      # readable formats available now
    def list_writable_formats(self) -> list[ArchiveFormat]: ...
```

If detection yields a format with no registered (available) read backend, the system
SHALL raise `UnsupportedFormatError` with the install hint (see graceful degradation).
If detection itself finds no format, it raises `FormatDetectionError` (see
`format-detection`).

#### Scenario: format mapped to its read backend

- **WHEN** `detect_format()` reports `ArchiveFormat.SEVEN_Z` and `reader_for_format(ArchiveFormat.SEVEN_Z)` is called
- **THEN** the native `SevenZReadBackend` is returned (it is always registered)

#### Scenario: detected format has no available backend

- **WHEN** detection yields a format whose backend's optional dependency is not installed
- **THEN** `reader_for_format()` raises `UnsupportedFormatError` naming the missing package and install hint

#### Scenario: source matches no format

- **WHEN** detection matches no magic and no recognisable extension
- **THEN** `detect_format()` raises `FormatDetectionError` and no backend lookup occurs

---

### Requirement: Separate ReadBackend and WriteBackend ABCs

Reading and writing are different concerns with different state, lifecycles, and even
availability (7z reading is native while writing needs `py7zr`; RAR has no writer at
all), so the system SHALL define **two** abstract base classes — `ReadBackend` and
`WriteBackend` — rather than one `Backend` with an optional write method. A format may
have a read backend, a write backend, both, or (RAR) only a reader. They are registered
in separate registries.

```python
class ReadBackend(ABC):
    FORMATS: tuple[ArchiveFormat, ...]      # formats this backend reads
    EXTENSIONS: tuple[str, ...]             # declared as data for the detector
    MAGIC: tuple[tuple[int, bytes], ...]    # (offset, bytes) pairs, consumed by the detector
    REQUIRES_SEEK: bool = False             # if True, non-seekable sources are rejected
    OPTIONAL_DEPENDENCY: str | None = None  # e.g. "pycdlib"

    @abstractmethod
    def open_read(
        self,
        source: Path | BinaryIO,            # a PeekableStream for non-seekable sources
        streaming: bool,
        password: bytes | None,
        encoding: str | None,
        archive_name: str | None,           # computed once by open_archive() (path or stream name)
    ) -> ArchiveReader: ...

class WriteBackend(ABC):
    FORMATS: tuple[ArchiveFormat, ...]      # formats this backend writes
    OPTIONAL_DEPENDENCY: str | None = None  # e.g. "py7zr" for 7z write

    @abstractmethod
    def open_write(
        self,
        dest: Path | BinaryIO,
        compression: CompressionSpec | None,
        password: bytes | None,
        encoding: str | None,
    ) -> ArchiveWriter: ...
```

`ReadBackend` declares its magic/extensions as **data** for the central detector (it has
no `detect(peek)` logic method — matching is centralized; see the detection/selection
requirement). `WriteBackend` is looked up by format via `writer_for_format()`; a format
with no registered write backend is unwritable and the attempt raises
`UnsupportedOperationError` (for native-read-only RAR) or `UnsupportedFormatError` with an
install hint (for 7z without `[7z-write]`).

#### Scenario: format with no write backend

- **WHEN** `archivey.create()` is called for a format that has a read backend but no registered write backend (e.g. RAR)
- **THEN** `UnsupportedOperationError` is raised with a message naming the format

#### Scenario: read backend with REQUIRES_SEEK given a non-seekable stream

- **WHEN** `open_read()` is invoked for a backend whose `REQUIRES_SEEK` is `True` and the source does not support `seek()`
- **THEN** the system raises `StreamNotSeekableError` (a subclass of `OpenError`) indicating a seekable source is required

---

### Requirement: Optional-dependency graceful degradation

The system SHALL degrade gracefully when an optional backend dependency is missing: the format is simply unavailable rather than causing an import crash. When a file of that format is subsequently opened, the system SHALL raise `UnsupportedFormatError` with a human-readable message that names the missing package and the install command.

When an ISO file is detected but `pycdlib` is not installed, `reader_for_format()` raises `UnsupportedFormatError` with a message of the form:

> "ISO 9660 format detected but backend is not installed. Run: pip install archivey[iso]"

The same pattern applies to Zstandard (`zstandard`, `[zstd]`) and LZ4 (`lz4`, `[lz4]`). Two capabilities degrade differently because 7z/RAR reading is native:

- **7-Zip writing** is gated on `py7zr` (`[7z-write]`); 7z *reading* is native and always available. A 7z write without the extra raises `UnsupportedOperationError` naming `[7z-write]`.
- **RAR data reading** needs the external `unrar` binary, not a Python package. Listing works natively without it; a data read without `unrar` raises a clear error naming the missing tool (see `format-rar`).

#### Scenario: ISO file opened without pycdlib

- **WHEN** a source file with the ISO 9660 magic is passed to `archivey.open_archive()` and `pycdlib` is not installed
- **THEN** `UnsupportedFormatError` is raised
- **AND** the error message names `pycdlib` and suggests `pip install archivey[iso]`
- **AND** no `ImportError` propagates to the caller

#### Scenario: list_formats() excludes unavailable formats

- **WHEN** `BackendRegistry.list_formats()` is called on a system where `pycdlib` is not installed
- **THEN** `ArchiveFormat.ISO` is absent from the returned list
- **AND** the native 7z and RAR readers are present, along with all formats whose dependencies are satisfied
