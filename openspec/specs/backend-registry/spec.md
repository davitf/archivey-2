# Backend Registry

## Purpose

The backend registry maps detected archive formats to stateless read/write
backend factories. It knows core and optional formats at import time, reports
availability with install hints, and keeps format detection separate from backend
selection.

## Related specs

| Spec | Relationship |
| --- | --- |
| `format-detection` | Central magic/probe/extension matching before registry lookup |
| `compressed-streams` | Codec descriptors and codec availability used in format support |
| `archive-reading` | `open_archive()` calls detection then registry selection |
| `error-handling` | `FormatDetectionError`, `UnsupportedFormatError`, `PackageNotInstalledError` |
| `packaging-and-extras` | Extras that satisfy optional dependencies |

## Requirements

### Requirement: Backends self-register at import time

The system SHALL register all known core and optional backends when `archivey`
imports. Optional library-backed backends (ISO, ZST, LZ4) and optional 7z writing
SHALL be imported behind `try/except ImportError` guards and registered
regardless of dependency availability. Missing dependencies make a format known
but unavailable, not unknown, and import MUST NOT fail.

Each backend SHALL declare dependency metadata as data. Availability SHALL be
derived centrally from the module-or-`None` sentinel idiom (`_optional("pycdlib")`
returns module or `None`), not per-backend booleans. 7z and RAR reading are
native and known; RAR data reads additionally require the external `unrar` binary
at read time, making RAR partial rather than import-unavailable.

```python
pycdlib = _optional("pycdlib")

class IsoReadBackend(ReadBackend):
    FORMATS = (ArchiveFormat.ISO,)
    EXTENSIONS = {".iso": ArchiveFormat.ISO}
    MAGIC = ((32769, b"CD001", ArchiveFormat.ISO),)
    OPTIONAL_DEPENDENCY = "pycdlib"

    def open_read(self, source, format, streaming, password, encoding, archive_name):
        assert pycdlib is not None
        ...

register_reader(IsoReadBackend)
```

The sentinel SHALL type-check cleanly under Pyrefly and ty because uses occur
behind `is not None` or equivalent registry-selection guarantees.

#### Scenario: import registration matrix

| Case | Expected |
| --- | --- |
| `import archivey` with no optional extras | Core formats plus native 7z/RAR register; supported formats show FULL/PARTIAL where applicable |
| `pycdlib` absent at import | No `ImportError`; ISO absent from supported formats but present in known formats with NONE support and install hint |

### Requirement: Backend classes are stateless factories

Each `ReadBackend` and `WriteBackend` subclass SHALL hold no per-archive state.
All archive state lives in the returned `ArchiveReader` or `ArchiveWriter`.
Multiple readers from the same backend class MUST be independent.

#### Scenario: stateless backend matrix

| Case | Expected |
| --- | --- |
| `open_archive("a.zip")` and `open_archive("b.zip")` are both open | Independent `ArchiveReader` instances; operations do not affect each other |

### Requirement: Detection owns matching and registry selects by format

The system SHALL keep format detection and backend selection separate.
`detect_format()` is the authority for source format: it aggregates backend
`MAGIC`, `EXTENSIONS`, and `CONTENT_PROBES`, performs special probes through
`PeekableStream`, consumes no bytes, and raises `FormatDetectionError` when no
format matches. The registry SHALL map the resolved `ArchiveFormat` to a
registered available backend. If a detected format has no available backend,
lookup SHALL raise `UnsupportedFormatError` with the install hint.

```python
class BackendRegistry:
    def register_reader(self, backend_cls: type[ReadBackend]) -> None: ...
    def reader_for_format(self, format: ArchiveFormat) -> type[ReadBackend]: ...
    def register_writer(self, backend_cls: type[WriteBackend]) -> None: ...
    def writer_for_format(self, format: ArchiveFormat) -> type[WriteBackend]: ...
    def list_formats(self) -> list[ArchiveFormat]: ...
    def list_writable_formats(self) -> list[ArchiveFormat]: ...
```

#### Scenario: detection/selection matrix

| Case | Expected |
| --- | --- |
| Detection reports `ArchiveFormat.SEVEN_Z` | `reader_for_format()` returns native `SevenZReadBackend` |
| Detected backend's optional dependency is missing | `UnsupportedFormatError` names missing package and install hint |
| No magic/probe/extension matches | `FormatDetectionError`; no backend lookup |

### Requirement: ReadBackend and WriteBackend are separate ABCs

The system SHALL define separate `ReadBackend` and `WriteBackend` ABCs because
read and write lifecycles, state, and availability differ. A format may have
read support, write support, both, or read-only support such as RAR.

Read backends SHALL declare all detection signals as data, and each signal SHALL
name the `ArchiveFormat` it implies so multi-format backends can map each
extension/magic/probe to the correct format without re-inspecting the source.
Magic-less or weak-signature formats SHALL use `CONTENT_PROBES` plus extensions;
there is no weak-magic flag.

```python
class MagicSignature(NamedTuple):
    offset: int
    magic: bytes
    format: ArchiveFormat

class ReadBackend(ABC):
    FORMATS: tuple[ArchiveFormat, ...]
    EXTENSIONS: Mapping[str, ArchiveFormat] = {}
    MAGIC: tuple[MagicSignature, ...] = ()
    CONTENT_PROBES: tuple[tuple[ArchiveFormat, Callable[[bytes], bool]], ...] = ()
    SUPPORTS_STREAMING_NON_SEEKABLE: bool = False
    OPTIONAL_DEPENDENCY: str | None = None

    @abstractmethod
    def open_read(self, source, format, streaming, password, encoding, archive_name) -> ArchiveReader: ...

class WriteBackend(ABC):
    FORMATS: tuple[ArchiveFormat, ...]
    OPTIONAL_DEPENDENCY: str | None = None

    @abstractmethod
    def open_write(self, dest, compression, password, encoding) -> ArchiveWriter: ...
```

The `format` argument SHALL be the already-resolved `ArchiveFormat`; multi-format
backends use it to choose a variant, while single-format backends may ignore it.
A missing write backend SHALL raise `UnsupportedOperationError` for read-only
formats or `UnsupportedFormatError` with an install hint for optional write
formats.

#### Scenario: backend ABC matrix

| Case | Expected |
| --- | --- |
| `SingleFileBackend.MAGIC` has gzip and bzip2 signatures | Detector resolves `GZ` vs `BZ2`; both are served by one backend |
| `archivey.create()` targets read-only RAR | `UnsupportedOperationError` names the format |

### Requirement: Optional dependencies degrade gracefully

The system SHALL degrade missing optional components to NONE or PARTIAL support
rather than import crashes. Opening a format or reading a member that needs a
missing component SHALL raise an error naming the package/tool and install
command from the same metadata exposed by `format_availability()`.

| Missing component kind | Support | Later error |
| --- | --- | --- |
| Single-codec format backend/codec missing (ISO without `pycdlib`, `.zst` without zstd backend before 3.14, `.lz4` without `lz4`) | NONE | `UnsupportedFormatError` at open with hint |
| Multi-codec container missing optional member codec/tool | PARTIAL | Opens/lists; member read raises `PackageNotInstalledError` or documented missing-tool error |
| 7z writing (not yet implemented) | Read support unaffected | Write raises `UnsupportedOperationError` |

#### Scenario: graceful degradation matrix

| Case | Expected |
| --- | --- |
| ISO magic source opened without `pycdlib` | `UnsupportedFormatError` names `pycdlib` and `pip install archivey[iso]`; no `ImportError` |
| `list_supported_formats()` without `pycdlib` | ISO absent; native 7z/RAR and satisfied formats present |

### Requirement: Codec availability and install hints come from descriptors

The system SHALL derive compositional support and missing-component install hints
from codec descriptor `requirement` fields. The separate `_CODEC_REQUIREMENT`
table SHALL not exist. FULL/PARTIAL/NONE results MUST remain unchanged by this
refactor.

#### Scenario: codec requirement matrix

| Case | Expected |
| --- | --- |
| Single-codec format's backend is missing | `format_availability()` reports NONE with descriptor-sourced install hint |
| ZIP / 7z / `tar.<codec>` availability is queried with missing optional codecs | Support level and missing list match the previous behavior, computed from descriptors |

### Requirement: Format support is tri-state and compositional

The system SHALL report readability as FULL, PARTIAL, or NONE:

```python
class FormatSupport(Enum):
    FULL = "full"
    PARTIAL = "partial"
    NONE = "none"

@dataclass(frozen=True)
class MissingComponent:
    name: str
    install_hint: str
    unlocks: tuple[str, ...]

@dataclass(frozen=True)
class FormatAvailability:
    format: ArchiveFormat
    support: FormatSupport
    missing: tuple[MissingComponent, ...]

def format_availability(format: ArchiveFormat) -> FormatAvailability: ...
def list_supported_formats() -> list[ArchiveFormat]: ...
def list_known_formats() -> list[ArchiveFormat]: ...
```

Support SHALL be computed across the format backend and codecs/tools:

- NONE when the format backend is unavailable, or a single-codec format's only
  codec/backend is unavailable.
- FULL for an available multi-codec container only when every optional codec/tool
  it can use is present.
- PARTIAL for available multi-codec containers with missing optional codecs/tools.
- ZIP SHALL remain PARTIAL until Phase 6 routes member decompression through the
  shared codec layer, even if all optional member-codec packages are installed.
- By-design unsupported features such as 7z BCJ2 and unknown 7z method IDs SHALL
  not lower support; members using them raise `UnsupportedFeatureError`.

`list_supported_formats()` SHALL return FULL plus PARTIAL formats.
`list_known_formats()` SHALL return every known format including NONE.

#### Scenario: format support matrix

| Case | Expected |
| --- | --- |
| 7z availability without `[7z]` or `[crypto]` | PARTIAL; missing names `[7z]` and `[crypto]`; LZMA2/bzip2/copy members still read |
| ZSTD availability before Python 3.14 without zstd backend | NONE with `[zstd]` / `pip install archivey[zstd]` hint |
| GZIP availability | FULL; no missing components |
| 7z with `[7z]` and `[crypto]` installed | FULL even though BCJ2 still raises `UnsupportedFeatureError` |
| ZIP with every optional member codec installed | PARTIAL with empty missing list until Phase 6 |
| ZIP missing deflate64 and/or zstd packages | PARTIAL; missing names absent codec packages; stored/deflate members still list/read |
