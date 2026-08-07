# backend-registry — `required_source` delta

## MODIFIED Requirements

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
    required_source: StreamCapability = StreamCapability.SEEKABLE

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

`required_source` SHALL report **the weakest source shape the format can be read
from**, so that the split between formats readable from a pipe and formats that must
seek is queryable as data rather than discovered by catching
`StreamNotSeekableError`. It SHALL be derived from the backend's
`SUPPORTS_STREAMING_NON_SEEKABLE` declaration — the same fact `open_archive()`
enforces — and MUST NOT be declared separately per backend:

| `SUPPORTS_STREAMING_NON_SEEKABLE` | `required_source` | Formats |
| --- | --- | --- |
| `True` | `StreamCapability.FORWARD_ONLY` | TAR and its compressed combos, the single-file compressors |
| `False` | `StreamCapability.SEEKABLE` | ZIP, ISO, 7z, RAR, directory |

`required_source` SHALL be reported independently of `support`: a format whose
optional dependency is missing still answers the source-shape question. For a format
with no registered backend at all, `required_source` SHALL be `SEEKABLE` — the
conservative answer.

#### Scenario: format support matrix

| Case | Expected |
| --- | --- |
| 7z availability without the optional 7z packages | PARTIAL; missing names each absent package and `[recommended]`; LZMA2/bzip2/copy members still read |
| ZSTD availability before Python 3.14 without zstd backend | NONE with `backports.zstd` / `pip install archivey[recommended]` hint |
| GZIP availability | FULL; no missing components |
| 7z with the optional 7z packages installed | FULL even though BCJ2 still raises `UnsupportedFeatureError` |
| ZIP with every optional member codec installed | PARTIAL with empty missing list until Phase 6 |
| ZIP missing deflate64 and/or zstd packages | PARTIAL; missing names absent codec packages; stored/deflate members still list/read |

#### Scenario: required source matrix

| Case | Expected |
| --- | --- |
| `format_availability(TAR).required_source` | `FORWARD_ONLY` |
| `format_availability(TAR_GZ).required_source` | `FORWARD_ONLY` |
| `format_availability(GZ).required_source` | `FORWARD_ONLY` |
| `format_availability(ZIP \| ISO \| SEVEN_Z \| RAR \| FOLDER).required_source` | `SEEKABLE` |
| ISO queried without `pycdlib` | `support=NONE` **and** `required_source=SEEKABLE` — the answer does not depend on installability |
| `required_source <= reader.cost.stream_capability` for a format opened successfully from that source | `True` for every format/source pair the library accepts |
