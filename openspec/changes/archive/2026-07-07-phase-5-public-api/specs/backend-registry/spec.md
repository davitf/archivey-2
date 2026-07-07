# backend-registry — Phase 5 deltas

## MODIFIED Requirements

### Requirement: Format support is tri-state and compositional

The system SHALL report the readability of each known format as one of three levels
rather than a binary available/unavailable flag, because a per-member multi-codec
container can be readable for its common members while lacking a rarely-used optional
codec:

```python
class FormatSupport(Enum):
    FULL    = "full"     # format backend usable AND every optional codec/tool it can use is present
    PARTIAL = "partial"  # opens & lists; common members decode; some optional codec/tool missing
    NONE    = "none"     # the format backend (or a single-codec format's sole codec) is unavailable
```

Support SHALL be computed **compositionally** across the *format backend* (registry
level) and the *codec backends* a format can use (the `compressed-streams` layer):

- A format is **NONE** if its format backend is unavailable, or — for a single-codec
  format (single-file compressors, `tar.<codec>`) — if that one codec backend is
  unavailable.
- A multi-codec container (7z, RAR) whose format backend is available is **FULL**
  when every optional codec/tool it can use is present, otherwise **PARTIAL**.
- **ZIP is an exception until Phase 7** (see `format-zip`): member *data* decompression
  still goes through stdlib `zipfile`, which cannot use deflate64/PPMd (or zstd before
  Python 3.14) even when the corresponding codec packages are installed. Therefore
  `format_availability(ArchiveFormat.ZIP)` SHALL report **PARTIAL** regardless of
  optional codec installation until Phase 7 wires the shared codec layer into ZIP
  member reads. When optional ZIP member-codec packages are absent, `missing` lists
  them as for any other multi-codec container; when every package is present, `missing`
  is empty — support remains `PARTIAL` because the read-time gap is implementation
  stage, not a missing install.
- **Missing-dependency** gaps (which determine PARTIAL/NONE) are distinct from
  **by-design** rejections. Codecs the library deliberately does not support — 7z
  **BCJ2** and unknown 7z method IDs — never count against FULL; a member using them
  raises `UnsupportedFeatureError` regardless of what is installed.

The system SHALL expose this via:

```python
@dataclass(frozen=True)
class MissingComponent:
    name: str              # package / extra / external tool, e.g. "pycdlib", "[7z]", "unrar"
    install_hint: str      # e.g. "pip install archivey[iso]"
    unlocks: tuple[str, ...]  # member-codecs/capabilities it would enable, e.g. ("ppmd",)

@dataclass(frozen=True)
class FormatAvailability:
    format: ArchiveFormat
    support: FormatSupport
    missing: tuple[MissingComponent, ...]   # empty when FULL

def list_supported_formats() -> list[ArchiveFormat]:  # FULL ∪ PARTIAL (readable now)
```

`list_supported_formats()` SHALL return formats with support FULL or PARTIAL;
`list_known_formats()` SHALL return every known format including NONE.

#### Scenario: 7z without optional codecs is partial

- **WHEN** `format_availability(ArchiveFormat.SEVEN_Z)` is queried on a system without `[7z]` or `[crypto]`
- **THEN** `support` is `FormatSupport.PARTIAL`
- **AND** `missing` names `[7z]` (unlocking ppmd/deflate64) and `[crypto]` (unlocking AES)
- **AND** opening a 7z archive whose members use only LZMA2/bzip2/copy succeeds, while reading a PPMd member raises `PackageNotInstalledError`

#### Scenario: single-codec format without its codec is none

- **WHEN** `format_availability(ArchiveFormat.ZSTD)` is queried and the zstd backend is not available (`backports.zstd` absent on Python < 3.14)
- **THEN** `support` is `FormatSupport.NONE` and `missing` names `[zstd]` / `pip install archivey[zstd]`

#### Scenario: fully-stdlib format is full

- **WHEN** `format_availability(ArchiveFormat.GZIP)` is queried
- **THEN** `support` is `FormatSupport.FULL` and `missing` is empty

#### Scenario: by-design-unsupported codec does not lower support

- **WHEN** `format_availability(ArchiveFormat.SEVEN_Z)` is queried on a system with `[7z]` and `[crypto]` installed
- **THEN** `support` is `FormatSupport.FULL` even though a 7z member using BCJ2 would still raise `UnsupportedFeatureError`

#### Scenario: ZIP reports partial until member-codec bypass lands

- **WHEN** `format_availability(ArchiveFormat.ZIP)` is queried on a system with every optional ZIP member codec installed (deflate64, PPMd, zstd)
- **THEN** `support` is `FormatSupport.PARTIAL` and `missing` is empty
- **AND** reading a deflate64/PPMd/zstd member still raises `UnsupportedFeatureError` until Phase 7 wires the codec layer into ZIP member reads

#### Scenario: ZIP partial when optional member codecs are missing

- **WHEN** `format_availability(ArchiveFormat.ZIP)` is queried on a system without deflate64 and/or zstd packages
- **THEN** `support` is `FormatSupport.PARTIAL`
- **AND** `missing` names the absent codec packages
- **AND** stored/deflate members still open and list successfully
