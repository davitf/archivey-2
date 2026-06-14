# Packaging and Extras

## Purpose

This capability defines the install-time contract for the library: that the core
installs with zero third-party dependencies, that each optional format is gated
behind a named extra, the supported Python and OS matrix, and how the installed
version is exposed at runtime. It is the install-time counterpart to
`backend-registry`, which owns the *runtime* behavior when an optional
dependency is present or absent (graceful degradation, `list_formats()`,
`UnsupportedFormatError`).

## Requirements

### Requirement: Zero-Dependency Core

The system SHALL install with no third-party runtime dependencies when no extras
are requested. With a bare `pip install archivey`, every format that has a native
or stdlib-backed reader MUST be fully functional: ZIP, TAR (plain and the
`tar.gz` / `tar.bz2` / `tar.xz` variants), the single-file GZ / BZ2 / XZ
compressors, the directory pseudo-backend, and **7-Zip reading** (native parser
over stdlib `lzma`/`bz2`/`zlib`). **RAR reading** is also core insofar as its
metadata is parsed natively, but reading RAR member *data* additionally requires
the external `unrar` binary at runtime (a system tool, not a pip dependency) —
see the `format-rar` spec.

The build is configured with the `hatchling` backend and targets the
distribution name `archivey`.

#### Scenario: core install pulls no third-party packages

- **WHEN** `pip install archivey` is run with no extras
- **THEN** no third-party runtime packages are installed
- **AND** ZIP, TAR (all stdlib variants), GZ/BZ2/XZ, directory, and 7-Zip **reading of common codecs** all work, and RAR listing works (the `[7z]` bundle adds PPMd/Deflate64/Zstd/Brotli/AES for 7z; `[rar]` adds encrypted RAR5 headers and Blake2sp verification; without them a member with only a Blake2sp hash still reads, just unverified; RAR data reads need the system `unrar` binary)

#### Scenario: writing 7z requires the [7z-write] extra

- **WHEN** the core-only install is used to *write* a `.7z` archive
- **THEN** the operation is unavailable until the `[7z-write]` extra is installed (see `backend-registry` for the exact `UnsupportedFormatError` + install-hint behavior); 7z *reading* still works with no extra

---

### Requirement: Optional Extras Enable Specific Formats

The system SHALL gate each optional capability behind a named install extra that
pulls in exactly the third-party dependency it requires. Because 7z and RAR
reading are native (no Python library for the common case), the library extras
cover only the less-common 7z codecs, encryption, 7z *writing*, ISO, the extra
compression formats, and the CLI. The mapping is:

| Extra | Pulls in | Enables |
|-------|----------|---------|
| *(none)* | stdlib only + native parsers | ZIP, TAR + `tar.gz`/`tar.bz2`/`tar.xz`, GZ, BZ2, XZ, directory, **7z read** (common codecs: LZMA/LZMA2/BCJ/Delta/Deflate/BZip2/STORED) with CRC32 verification, **RAR metadata/listing** with CRC32 verification (Blake2sp verification needs `[rar]`) — RAR member *data* needs the system `unrar` binary |
| `[7z]` | `pyppmd`, `inflate64`, `zstandard`, `brotli`, `cryptography` | **all** 7z reading features — PPMd, Deflate64, Zstd, Brotli, and AES-encrypted 7z |
| `[rar]` | `cryptography`, a Blake2sp backend | **all** RAR reading features that need a Python package — header-encrypted RAR5 and Blake2sp checksum verification (member *data* still needs the `unrar` binary) |
| `[crypto]` | `cryptography` | the AES/crypto backend alone — a subset of `[7z]`/`[rar]`, for callers who want only encryption support |
| `[7z-write]` | `py7zr` | 7-Zip **writing** (reading is native, no extra) |
| `[iso]` | `pycdlib` | ISO 9660 (`.iso`) |
| `[zstd]` | `zstandard` | standalone Zstandard (`.zst`, `.tar.zst`) |
| `[lz4]` | `lz4` | LZ4 (`.tar.lz4`) |
| `[cli]` | `tqdm` | the `archivey` command-line interface and its progress bar |
| `[all]` | every optional runtime dependency above | every optional capability |

`[7z]` and `[rar]` are **format bundles**: installing `[7z]` enables every 7z
reading feature and `[rar]` every RAR reading feature that needs a Python package,
so a user does not have to assemble per-codec extras to fully read a format. They
are supersets of the finer-grained `[crypto]`/`[zstd]` extras, which remain for
callers who want only those backends. RAR member *data* decompression always needs
the external `unrar` binary at runtime regardless of extras (see `format-rar`);
`[rar]` only supplies the Python-side dependencies (`cryptography` and a Blake2sp
backend); RAR5 member *data* still needs the `unrar` binary.

The bundles are a convenience for getting **complete** support in one install; the
readers do **not** require them to operate. With optional libs missing, a reader
still processes the archive and degrades along a single rule: it raises (a
`PackageNotInstalledError` or `UnsupportedFeatureError`) only when it cannot
**produce** a member's bytes — a codec or crypto backend that is genuinely absent,
or an unsupported feature — and it merely **skips** any integrity check it cannot
**compute** (e.g. a member's Blake2sp hash when the Blake2sp backend is absent),
emitting an integrity warning rather than failing the read. So a RAR5 archive whose
members carry only Blake2sp hashes still reads without `[rar]`; the data is just
returned unverified with a warning.

The `py7zr` and `rarfile` libraries are otherwise used only as test oracles and live
in the `dev` dependency group. BCJ2-filtered 7z members are not supported by any
extra.

Installing an extra MUST make its capability available without requiring any other
extra. The `[all]` extra MUST be equivalent to installing every individual
runtime extra. This contract is package-manager-agnostic — `pip install
archivey[iso]` and `uv add archivey --extra iso` (or `uv pip install`) honor the
same extras.

Development-only tooling (test / lint / type-check) and the test-oracle libraries
(`py7zr`, `rarfile`) are NOT runtime extras. They are declared as a PEP 735
`[dependency-groups]` entry (`dev`), so they are never installed for end users and
are pulled in by `uv sync` (or `pip install --group dev`) for contributors.

#### Scenario: installing an extra enables exactly its capability

- **WHEN** `pip install archivey[iso]` is run
- **THEN** `pycdlib` is installed and `.iso` archives become available
- **AND** no other optional dependency (py7zr, zstandard, lz4) is pulled in

#### Scenario: `[all]` enables every optional capability

- **WHEN** `pip install archivey[all]` is run
- **THEN** every optional capability (7z PPMd/Deflate64/Zstd/Brotli/AES, encrypted RAR5 + Blake2sp verification, 7z write, ISO, ZST, LZ4, CLI) is available

#### Scenario: RAR reading requires only the system unrar binary

- **WHEN** a core install is used to read RAR data but no `unrar` binary is on PATH
- **THEN** RAR listing still works (native metadata), but RAR data reads fail with a clear error indicating the external `unrar` tool is required — no pip extra would fix this

---

### Requirement: Supported Runtime Environment

The system SHALL declare and support Python 3.11 or newer on Linux, macOS, and
Windows. The public API is synchronous only for v1, and readers and writers are
not thread-safe (one per thread).

#### Scenario: install rejected on unsupported Python

- **WHEN** installation is attempted on a Python interpreter older than 3.11
- **THEN** the `requires-python` constraint (`>=3.11`) prevents installation

#### Scenario: supported on all three operating systems

- **WHEN** the library is installed on Linux, macOS, or Windows under Python 3.11+
- **THEN** the core and any installed optional formats are supported on that platform

---

### Requirement: Version Metadata Exposure

The system SHALL expose its installed version as `archivey.__version__`, resolved
from the installed distribution metadata via `importlib.metadata` rather than a
hard-coded string.

#### Scenario: __version__ reflects the installed distribution

- **WHEN** a caller reads `archivey.__version__`
- **THEN** it returns the version recorded in the installed package metadata (e.g. `"0.2.0"`)
