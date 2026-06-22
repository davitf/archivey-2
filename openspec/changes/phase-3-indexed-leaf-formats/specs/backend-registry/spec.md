# Backend Registry — delta (Phase 3)

This change reworks how the registry tracks availability. Optional backends are no
longer dropped at import; they register unconditionally and carry an availability
flag, so the registry can report a tri-state, compositional **format support** and
produce the install-hint errors the capability already promises.

## MODIFIED Requirements

### Requirement: Backends self-register at import time

The system SHALL register **all** known core and optional backends when `archivey`
is imported, without any user action. Optional, library-backed backends (ISO, ZST,
LZ4) and the optional 7-Zip *writing* capability SHALL attempt their import inside a
`try/except ImportError` guard and register **regardless of the outcome**, recording
whether the dependency is present (an availability flag) together with the backend's
`OPTIONAL_DEPENDENCY` and install hint. A missing dependency therefore makes a format
*unavailable*, not *unknown*: the registry still knows the format exists and can name
the package to install. Import MUST NOT raise when an optional dependency is absent.

Note: 7-Zip and RAR *reading* are **native** and always available (no optional
dependency). RAR data reads additionally require the external `unrar` binary at
runtime — a missing-tool condition that lowers RAR to *partial* support (listing
works, data does not), handled at read time, not via an import guard (see
`format-rar`).

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

# Always registered; the registry records availability = _PYCDLIB_AVAILABLE so an
# absent pycdlib yields a NONE-support ISO with an install hint rather than a silent
# "unknown format".
register_reader(IsoReadBackend, available=_PYCDLIB_AVAILABLE)
```

#### Scenario: core backend available without extras

- **WHEN** `import archivey` succeeds on a system with no optional extras installed
- **THEN** ZIP, TAR (all variants), GZ, BZ2, XZ, Directory, and the native 7z and RAR readers are registered and appear in `BackendRegistry.list_formats()` with FULL or PARTIAL support

#### Scenario: optional backend absent at import is known but unavailable

- **WHEN** `pycdlib` is not installed and `archivey` is imported
- **THEN** no `ImportError` is raised during import
- **AND** `ArchiveFormat.ISO` is absent from `BackendRegistry.list_formats()`
- **AND** `ArchiveFormat.ISO` is present in `BackendRegistry.list_known_formats()` with support `NONE` and a missing-component hint naming `pycdlib` / `pip install archivey[iso]`

### Requirement: Optional-dependency graceful degradation

The system SHALL degrade gracefully when an optional dependency is missing: the
affected format becomes *unavailable* or *partially available* rather than causing an
import crash. When such a format is subsequently opened (or a member needing the
missing piece is read), the system SHALL raise an error whose message names the
missing package/tool and the install command, derived from the same availability
metadata exposed by `format_availability()`.

- A **single-codec** format whose sole codec/backend is missing (ISO without
  `pycdlib`; `.zst` without `zstandard`; `.lz4` without `lz4`) has support **NONE**;
  opening it raises `UnsupportedFormatError` with the install hint.
- A **multi-codec container** missing only some optional codecs/tools has support
  **PARTIAL**; it opens and lists, and only a member using the missing codec/tool
  raises `PackageNotInstalledError` (or, for RAR data without `unrar`, the documented
  missing-tool error) at read time.
- **7-Zip writing** is gated on `py7zr` (`[7z-write]`); 7z *reading* is native. A 7z
  write without the extra raises `UnsupportedOperationError` naming `[7z-write]`.

#### Scenario: ISO file opened without pycdlib

- **WHEN** a source with the ISO 9660 magic is passed to `archivey.open_archive()` and `pycdlib` is not installed
- **THEN** `UnsupportedFormatError` is raised, its message names `pycdlib` and suggests `pip install archivey[iso]`, and no `ImportError` propagates

#### Scenario: list_formats() excludes NONE-support formats

- **WHEN** `BackendRegistry.list_formats()` is called on a system where `pycdlib` is not installed
- **THEN** `ArchiveFormat.ISO` is absent (support NONE)
- **AND** the native 7z and RAR readers are present (FULL or PARTIAL), along with all formats whose dependencies are satisfied

## ADDED Requirements

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
- A multi-codec container (7z, ZIP, RAR) whose format backend is available is **FULL**
  when every optional codec/tool it can use is present, otherwise **PARTIAL**.
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

def format_availability(format: ArchiveFormat) -> FormatAvailability: ...
def list_formats() -> list[ArchiveFormat]:        # FULL ∪ PARTIAL (readable now)
def list_known_formats() -> list[ArchiveFormat]:  # every format the registry knows
```

`list_formats()` SHALL return formats with support FULL or PARTIAL;
`list_known_formats()` SHALL return every known format including NONE.

#### Scenario: 7z without optional codecs is partial

- **WHEN** `format_availability(ArchiveFormat.SEVEN_Z)` is queried on a system without `[7z]` or `[crypto]`
- **THEN** `support` is `FormatSupport.PARTIAL`
- **AND** `missing` names `[7z]` (unlocking ppmd/deflate64) and `[crypto]` (unlocking AES)
- **AND** opening a 7z archive whose members use only LZMA2/bzip2/copy succeeds, while reading a PPMd member raises `PackageNotInstalledError`

#### Scenario: single-codec format without its codec is none

- **WHEN** `format_availability(ArchiveFormat.ZSTD)` is queried and `zstandard` is not installed
- **THEN** `support` is `FormatSupport.NONE` and `missing` names `[zstd]` / `pip install archivey[zstd]`

#### Scenario: fully-stdlib format is full

- **WHEN** `format_availability(ArchiveFormat.GZIP)` is queried
- **THEN** `support` is `FormatSupport.FULL` and `missing` is empty

#### Scenario: by-design-unsupported codec does not lower support

- **WHEN** `format_availability(ArchiveFormat.SEVEN_Z)` is queried on a system with `[7z]` and `[crypto]` installed
- **THEN** `support` is `FormatSupport.FULL` even though a 7z member using BCJ2 would still raise `UnsupportedFeatureError`
