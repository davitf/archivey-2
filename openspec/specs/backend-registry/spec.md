# Backend Registry

## Purpose

The backend registry maps archive formats to the backend classes that can read or write them. Core backends are registered automatically at import time; optional backends register themselves only when their third-party dependency is available. The registry exposes a selection API used internally by `archivey.open_archive()`, `archivey.create()`, and `detect_format()`.

## Requirements

### Requirement: Backends self-register at import time

The system SHALL register core backends (ZIP, TAR, GZ/BZ2/XZ single-file compressors, Directory) automatically when `archivey` is imported, without any user action.

The system SHALL register library-backed optional backends (ISO, ZST, LZ4) and the optional 7-Zip *writing* capability inside a `try/except ImportError` guard. If the optional dependency is absent the guard catches the `ImportError` and the backend/capability is not registered; it does not raise at import time.

Note: 7-Zip and RAR *reading* are **native** and always registered (no import guard). RAR data reads additionally require the external `unrar` binary at runtime — a missing-tool condition handled at read time, not via an import guard (see `format-rar`).

```python
# backends/_iso.py
try:
    import pycdlib
    _PYCDLIB_AVAILABLE = True
except ImportError:
    _PYCDLIB_AVAILABLE = False

class IsoBackend(Backend):
    OPTIONAL_DEPENDENCY = "pycdlib"
    FORMAT = ArchiveFormat.ISO

    @classmethod
    def detect(cls, peek: bytes) -> bool:
        if not _PYCDLIB_AVAILABLE:
            return False   # don't claim the format if we can't handle it
        return peek[32769:32774] == b'CD001'
```

Backends register themselves by calling `archivey.backends.register(BackendClass)`.

#### Scenario: core backend available without extras

- **WHEN** `import archivey` succeeds on a system with no optional extras installed
- **THEN** ZIP, TAR (all variants), GZ, BZ2, XZ, Directory, and the native 7z and RAR readers are registered and their formats appear in `BackendRegistry.list_formats()`

#### Scenario: optional backend absent at import

- **WHEN** `pycdlib` is not installed and `archivey` is imported
- **THEN** no `ImportError` is raised during import
- **AND** `ArchiveFormat.ISO` does not appear in `BackendRegistry.list_formats()`

---

### Requirement: Backend is a stateless factory

The system SHALL implement each `Backend` subclass as a stateless factory: the class holds no per-archive state. All archive state lives in the `ArchiveReader` or `ArchiveWriter` instance returned by `open_read()` or `open_write()`. This allows multiple readers to be open simultaneously from the same backend class and simplifies testing by making it possible to mock `Backend.open_read()` to return a fake reader.

#### Scenario: two simultaneous readers from the same backend

- **WHEN** `archivey.open_archive("a.zip")` and `archivey.open_archive("b.zip")` are both open at the same time
- **THEN** each call returns an independent `ArchiveReader` instance with its own state
- **AND** operations on one reader do not affect the other

---

### Requirement: Backend selection by peek bytes, path, and intent

The system SHALL select a backend by calling `BackendRegistry.detect_backend(peek, path, intent)`. The registry iterates registered backends in registration order and returns the first whose `detect(peek)` method returns `True`. If no backend matches the peek bytes and a path is available, the registry may fall back to extension-based matching. If no backend can be found, the system SHALL raise `UnsupportedFormatError`.

The internal registry API is:

```python
class BackendRegistry:
    def register(self, backend_cls: type[Backend]) -> None: ...
    def detect_backend(self, peek: bytes, path: Path | None, intent: Intent) -> type[Backend]: ...
    def get_writer_backend(self, format: ArchiveFormat) -> type[Backend]: ...
    def list_formats(self) -> list[ArchiveFormat]: ...  # only available formats
```

#### Scenario: backend selected by magic bytes

- **WHEN** `detect_backend()` is called with peek bytes matching the 7-Zip magic (`37 7A BC AF 27 1C`)
- **THEN** `SevenZBackend` is returned (the native 7z reader is always registered)

#### Scenario: no backend matches

- **WHEN** `detect_backend()` is called with peek bytes that match no registered backend and no recognisable extension
- **THEN** the system raises `UnsupportedFormatError`

---

### Requirement: Backend ABC contract

The system SHALL define a `Backend` abstract base class that every backend must subclass. Backends declare their capabilities via class-level attributes and implement the abstract methods below.

```python
class Backend(ABC):
    FORMAT: ArchiveFormat              # primary format
    FORMATS: tuple[ArchiveFormat, ...]  # all formats this backend handles
    EXTENSIONS: tuple[str, ...]
    MAGIC: bytes
    MAGIC_OFFSET: int = 0
    REQUIRES_SEEK: bool = False        # if True, non-seekable streams are rejected
    SUPPORTS_WRITE: bool = False
    OPTIONAL_DEPENDENCY: str | None = None  # e.g. "pycdlib"

    @classmethod
    def detect(cls, peek: bytes) -> bool:
        """Return True if peek bytes match this format's magic."""
        ...

    @abstractmethod
    def open_read(
        self,
        source: Path | BinaryIO,
        intent: Intent,
        password: bytes | None,
        encoding: str,
    ) -> ArchiveReader: ...

    def open_write(
        self,
        dest: Path | BinaryIO,
        compression: CompressionSpec | None,
        password: bytes | None,
        encoding: str,
    ) -> ArchiveWriter:
        raise UnsupportedOperationError(f"{self.FORMAT} write not supported")
```

`open_read()` is abstract and must be implemented by every backend. `open_write()` has a default implementation that raises `UnsupportedOperationError`; backends that support writing override it.

#### Scenario: backend with SUPPORTS_WRITE=False called for writing

- **WHEN** `open_write()` is called on a backend whose `SUPPORTS_WRITE` is `False`
- **THEN** `UnsupportedOperationError` is raised with a message naming the format

#### Scenario: backend with REQUIRES_SEEK=True given a non-seekable stream

- **WHEN** `open_read()` is called on a backend whose `REQUIRES_SEEK` is `True` and the source stream does not support `seek()`
- **THEN** the system raises `OpenError` (or a subclass) indicating that a seekable source is required

---

### Requirement: Optional-dependency graceful degradation

The system SHALL degrade gracefully when an optional backend dependency is missing: the format is simply unavailable rather than causing an import crash. When a file of that format is subsequently opened, the system SHALL raise `UnsupportedFormatError` with a human-readable message that names the missing package and the install command.

When an ISO file is detected but `pycdlib` is not installed, `BackendRegistry.detect_backend()` raises `UnsupportedFormatError` with a message of the form:

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
