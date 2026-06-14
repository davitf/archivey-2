# Backend Registry

## Purpose

The backend registry maps archive formats to the backend classes that can read or write them. Core backends are registered automatically at import time; optional backends register themselves only when their third-party dependency is available. The registry exposes a selection API used internally by `archivey.open()`, `archivey.create()`, and `detect_format()`.

## Requirements

### Requirement: Backends self-register at import time

The system SHALL register core backends (ZIP, TAR, GZ/BZ2/XZ single-file compressors, Directory) automatically when `archivey` is imported, without any user action.

The system SHALL register optional backends (7z, RAR, ISO, ZST) inside a `try/except ImportError` guard. If the optional dependency is absent the guard catches the `ImportError` and the backend is not registered; it does not raise at import time.

```python
# backends/_7z.py
try:
    import py7zr
    _PY7ZR_AVAILABLE = True
except ImportError:
    _PY7ZR_AVAILABLE = False

class SevenZBackend(Backend):
    OPTIONAL_DEPENDENCY = "py7zr"
    FORMAT = ArchiveFormat.SEVEN_Z

    @classmethod
    def detect(cls, peek: bytes) -> bool:
        if not _PY7ZR_AVAILABLE:
            return False   # don't claim the format if we can't handle it
        return peek[:6] == b'7z\xbc\xaf\x27\x1c'
```

Backends register themselves by calling `archivey.backends.register(BackendClass)`.

#### Scenario: core backend available without extras

- **WHEN** `import archivey` succeeds on a system with no optional extras installed
- **THEN** ZIP, TAR (all variants), GZ, BZ2, XZ, and Directory backends are registered and their formats appear in `BackendRegistry.list_formats()`

#### Scenario: optional backend absent at import

- **WHEN** `py7zr` is not installed and `archivey` is imported
- **THEN** no `ImportError` is raised during import
- **AND** `ArchiveFormat.SEVEN_Z` does not appear in `BackendRegistry.list_formats()`

---

### Requirement: Backend is a stateless factory

The system SHALL implement each `Backend` subclass as a stateless factory: the class holds no per-archive state. All archive state lives in the `ArchiveReader` or `ArchiveWriter` instance returned by `open_read()` or `open_write()`. This allows multiple readers to be open simultaneously from the same backend class and simplifies testing by making it possible to mock `Backend.open_read()` to return a fake reader.

#### Scenario: two simultaneous readers from the same backend

- **WHEN** `archivey.open("a.zip")` and `archivey.open("b.zip")` are both open at the same time
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

- **WHEN** `detect_backend()` is called with peek bytes matching the 7-Zip magic (`37 7A BC AF 27 1C`) and `py7zr` is installed
- **THEN** `SevenZBackend` is returned

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
    OPTIONAL_DEPENDENCY: str | None = None  # e.g. "py7zr"

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

When a 7z file is detected but `py7zr` is not installed, `BackendRegistry.detect_backend()` raises `UnsupportedFormatError` with a message of the form:

> "7-Zip format detected but backend is not installed. Run: pip install archivey[7z]"

The same pattern applies to RAR (`rarfile` + `unrar` binary), ISO (`pycdlib`), and Zstandard (`zstandard`).

#### Scenario: 7z file opened without py7zr

- **WHEN** a source file with the 7-Zip magic header is passed to `archivey.open()` and `py7zr` is not installed
- **THEN** `UnsupportedFormatError` is raised
- **AND** the error message names `py7zr` and suggests `pip install archivey[7z]`
- **AND** no `ImportError` propagates to the caller

#### Scenario: list_formats() excludes unavailable formats

- **WHEN** `BackendRegistry.list_formats()` is called on a system where `py7zr` is not installed
- **THEN** `ArchiveFormat.SEVEN_Z` is absent from the returned list
- **AND** all formats whose dependencies are satisfied are present
