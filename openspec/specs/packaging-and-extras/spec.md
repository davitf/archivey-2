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
are requested. With a bare `pip install archivey`, the formats backed by the
Python standard library MUST be fully functional: ZIP, TAR (plain and the
`tar.gz` / `tar.bz2` / `tar.xz` variants), the single-file GZ / BZ2 / XZ
compressors, and the directory pseudo-backend.

The build is configured with the `hatchling` backend and targets the
distribution name `archivey`.

#### Scenario: core install pulls no third-party packages

- **WHEN** `pip install archivey` is run with no extras
- **THEN** no third-party runtime packages are installed
- **AND** ZIP, TAR (all stdlib variants), GZ/BZ2/XZ, and directory archives can be opened, read, and extracted

#### Scenario: optional format unavailable without its extra

- **WHEN** the core-only install is used to open a `.7z` archive
- **THEN** the format is unavailable at runtime (see `backend-registry` for the exact `UnsupportedFormatError` + install-hint behavior)

---

### Requirement: Optional Extras Enable Specific Formats

The system SHALL gate each optional format behind a named install extra that
pulls in exactly the third-party dependency that format requires. The mapping is:

| Extra | Pulls in | Enables |
|-------|----------|---------|
| *(none)* | stdlib only | ZIP, TAR + `tar.gz`/`tar.bz2`/`tar.xz`, GZ, BZ2, XZ, directory |
| `[7z]` | `py7zr` | 7-Zip (`.7z`) |
| `[rar]` | `rarfile` (+ system `unrar` binary on PATH) | RAR (read-only) |
| `[iso]` | `pycdlib` | ISO 9660 (`.iso`) |
| `[zstd]` | `zstandard` | Zstandard (`.zst`, `.tar.zst`) |
| `[lz4]` | `lz4` | LZ4 (`.tar.lz4`) |
| `[cli]` | `tqdm` | the `archivey` command-line interface and its progress bar |
| `[all]` | every optional runtime dependency above | every optional format |

Installing an extra MUST make its format(s) available without requiring any other
extra. The `[all]` extra MUST be equivalent to installing every individual
runtime extra. This contract is package-manager-agnostic — `pip install
archivey[7z]` and `uv add archivey --extra 7z` (or `uv pip install`) honor the
same extras.

Development-only tooling (test / lint / type-check) is NOT a runtime extra. It is
declared as a PEP 735 `[dependency-groups]` entry (`dev`), so it is never
installed for end users and is pulled in by `uv sync` (or `pip install
--group dev`) for contributors.

#### Scenario: installing an extra enables exactly its format

- **WHEN** `pip install archivey[7z]` is run
- **THEN** `py7zr` is installed and `.7z` archives become available
- **AND** no other optional dependency (rarfile, pycdlib, zstandard, lz4) is pulled in

#### Scenario: `[all]` enables every optional format

- **WHEN** `pip install archivey[all]` is run
- **THEN** every optional format (7z, RAR, ISO, ZST, LZ4) is available

#### Scenario: RAR additionally requires the system unrar binary

- **WHEN** `archivey[rar]` is installed but no `unrar` binary is on PATH
- **THEN** RAR support is not fully functional (the `rarfile` dependency alone is insufficient; the external `unrar` tool is required)

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
