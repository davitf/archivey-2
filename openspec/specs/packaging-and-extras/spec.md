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
- **AND** ZIP, TAR (all stdlib variants), GZ/BZ2/XZ, directory, and 7-Zip **reading** all work, and RAR listing works (RAR data reads also require the system `unrar` binary)

#### Scenario: writing 7z requires the [7z-write] extra

- **WHEN** the core-only install is used to *write* a `.7z` archive
- **THEN** the operation is unavailable until the `[7z-write]` extra is installed (see `backend-registry` for the exact `UnsupportedFormatError` + install-hint behavior); 7z *reading* still works with no extra

---

### Requirement: Optional Extras Enable Specific Formats

The system SHALL gate each optional capability behind a named install extra that
pulls in exactly the third-party dependency it requires. Because 7z and RAR
reading are native (no Python library), the only library extras left are for ISO,
the extra compression codecs, 7z *writing*, and the CLI. The mapping is:

| Extra | Pulls in | Enables |
|-------|----------|---------|
| *(none)* | stdlib only + native parsers | ZIP, TAR + `tar.gz`/`tar.bz2`/`tar.xz`, GZ, BZ2, XZ, directory, **7z read**, **RAR metadata/listing** |
| `[7z-write]` | `py7zr` | 7-Zip **writing** (reading is native, no extra) |
| `[iso]` | `pycdlib` | ISO 9660 (`.iso`) |
| `[zstd]` | `zstandard` | Zstandard (`.zst`, `.tar.zst`) |
| `[lz4]` | `lz4` | LZ4 (`.tar.lz4`) |
| `[cli]` | `tqdm` | the `archivey` command-line interface and its progress bar |
| `[all]` | every optional runtime dependency above | every optional capability |

There is **no `[rar]` extra**: RAR reading needs no Python package, only the
external `unrar` binary at runtime (see `format-rar`). The `py7zr` and `rarfile`
libraries are otherwise used only as test oracles and live in the `dev`
dependency group.

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
- **THEN** every optional capability (7z write, ISO, ZST, LZ4, CLI) is available

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
