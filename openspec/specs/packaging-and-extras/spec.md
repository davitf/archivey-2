# Packaging and Extras

## Purpose

Install-time contract for Archivey: zero-dependency core, named optional extras,
supported runtimes, version exposure, and package layout. Runtime behavior when an
optional backend or system tool is absent belongs to `backend-registry` and the
format specs.

## Related specs

| Spec | Relationship |
| --- | --- |
| `backend-registry` | Runtime registration, graceful degradation, install hints |
| `format-7z` | Native 7z reading, optional codecs, AES, unsupported BCJ2 |
| `format-rar` | Native RAR metadata, `unrar` data path, crypto/checksum extras |
| `archive-reading` | Reader API exposed by core installs |
| `archive-writing` | 7z writing and writer codec availability |
| `access-mode-and-cost` | Seekable gzip/bzip2 capability and access-cost reporting |
| `reader-concurrency` | Supported `MemberStreams.CONCURRENT` contract |
| `cli` | `[cli]` extra and command-line dependency |

## Requirements

### Requirement: Zero-dependency core

The system SHALL install with no third-party runtime dependencies when no extras are
requested. Bare `pip install archivey` MUST fully support every native or
stdlib-backed reader: ZIP, TAR including `tar.gz` / `tar.bz2` / `tar.xz`, single-file
GZ / BZ2 / XZ, directories, and 7z reading for common codecs
(LZMA/LZMA2/BCJ/Delta/Deflate/BZip2/STORED) with CRC32 verification.

The system SHALL parse RAR metadata/listing natively in core with CRC32
verification. Reading RAR member data additionally requires the external `unrar`
system binary at runtime; no pip extra supplies that binary. RAR members that carry
only Blake2sp hashes still read without `[rar]`, but the Blake2sp integrity check is
skipped with a diagnostic/warning.

The build SHALL use `hatchling` and the distribution name `archivey`.

#### Scenario: core install matrix

| Case | Expected |
| --- | --- |
| `pip install archivey` with no extras | No third-party runtime packages installed |
| Core read of ZIP/TAR/GZ/BZ2/XZ/directory/common-codec 7z | Fully functional |
| Core RAR listing | Native metadata/listing works |
| Core RAR data read with no `unrar` on `PATH` | Clear error says the external `unrar` tool is required |
| Core-only 7z write | Unavailable until `[7z-write]`; 7z reading still works |

### Requirement: Optional extras enable specific capabilities

The system SHALL gate each optional capability behind a named extra that pulls the
third-party dependency required for that capability. 7z and RAR reading are native
for the common case, so extras cover less-common 7z codecs, encryption, 7z writing,
ISO, extra compression formats, seeking accelerators, and the CLI.

| Extra | Pulls in | Enables |
| --- | --- | --- |
| *(none)* | stdlib only + native parsers | ZIP, TAR + stdlib compressed TAR variants, GZ, BZ2, XZ, directory, 7z read for common codecs (including LZMA2+BCJ), RAR metadata/listing; RAR data still needs RARLAB `unrar` |
| `[7z]` | `pyppmd`, `inflate64`, `backports.zstd` on Python <3.14, `brotli`, `cryptography`, `pybcj` | All 7z reading features: PPMd, Deflate64, Zstd, Brotli, AES, LZMA1+BCJ |
| `[rar]` | `cryptography`, Blake2sp backend | Header-encrypted RAR5 and Blake2sp checksum verification; RAR data still needs RARLAB `unrar` |
| `[crypto]` | `cryptography` | AES/crypto backend subset used by `[7z]` / `[rar]` |
| `[7z-write]` | `py7zr` | 7z writing only; reading remains native |
| `[iso]` | `pycdlib` | ISO 9660 (`.iso`) |
| `[zstd]` | `backports.zstd` on Python <3.14 | Standalone Zstandard (`.zst`, `.tar.zst`); Python 3.14+ uses stdlib `compression.zstd` |
| `[lz4]` | `lz4` | LZ4 (`.tar.lz4`) |
| `[unix-compress]` | `uncompresspy` | Unix-compress (`.Z`, `.tar.Z`) LZW decompression |
| `[cli]` | `tqdm` | `archivey` command-line interface progress output |
| `[seekable]` | `rapidgzip` | Faster gzip/bzip2 decompression and random access into gz/bz2 streams via rapidgzip / bundled `IndexedBzip2File` |
| `[recommended-lite]` | `[7z]` + `[rar]` + `[7z-write]` + `[iso]` + `[zstd]` + `[lz4]` + `[unix-compress]` + `[cli]` | Every broadly wheeled format/codec dependency; excludes build-finicky C++ seek libs |
| `[recommended]` | `[recommended-lite]` + `[seekable]` | Recommended install: every primary backend plus gz/bz2 seeking and speed |
| `[all]` | `[recommended]` plus every alternative/secondary backend, currently none | Everything; currently resolves exactly to `[recommended]` |

The system SHALL make `[recommended]` the sensible all-useful install and
`[recommended-lite]` the fallback when `rapidgzip` cannot build. `[recommended-lite]`
MUST retain every format and codec except gz/bz2 seeking and the speed boost from
`[seekable]`.

The system SHALL keep `[all]` as a future-proof superset for redundant alternative
backends. At present `[all]` MUST resolve to exactly `[recommended]`; the former
`python-xz` and `pyzstd` alternatives are not pinned because the compression-library
analysis dropped them.

The system SHALL treat `[7z]` and `[rar]` as format bundles for complete read support
that requires Python packages. Missing optional libraries MUST degrade by one rule:
raise `PackageNotInstalledError` or `UnsupportedFeatureError` only when bytes cannot
be produced, and skip any integrity check that cannot be computed with an integrity
diagnostic/warning instead of failing the read.

The system SHALL keep `py7zr` and `rarfile` as dev-only test oracles except for
`py7zr` under `[7z-write]`. BCJ2-filtered 7z members MUST remain unsupported by every
extra. Installing any individual extra MUST make that capability available without
requiring unrelated extras. `[all]` MUST be equivalent to installing every runtime
extra. No user-facing extra SHALL pull an alternate RAR decompressor library or tool
wrapper.

Development tools, oracle libraries, and fixture generators such as `ncompress`
SHALL live in the PEP 735 `dev` dependency group, not in user-facing runtime extras.

#### Scenario: extras matrix

| Case | Expected |
| --- | --- |
| `pip install archivey[iso]` | Installs `pycdlib`; `.iso` works; unrelated optional deps are not pulled in |
| `pip install archivey[recommended]` | Every optional format/codec and CLI capability plus `[seekable]`; no redundant xz/zstd alternative backend |
| `pip install archivey[recommended-lite]` after `[recommended]` cannot build `rapidgzip` | Every format/codec still works; only gz/bz2 seeking and speed boost are absent |
| `pip install archivey[all]` | Installs `[recommended]` plus current alternatives; currently exactly `[recommended]` |
| `pip install archivey[7z]` | Installs `pybcj` (import name `bcj`) so LZMA1+BCJ 7z members decode |
| RAR5 data with only Blake2sp hashes and no `[rar]` | Bytes are returned unverified with a warning; no hard failure solely for skipped Blake2sp |
| 7z member uses BCJ2 | Unsupported-codec error; no extra enables it |
| RAR data without RARLAB `unrar` | `PackageNotInstalledError`; no alternate-tool extra exists |

### Requirement: RAR data uses RARLAB unrar only

The system SHALL treat RARLAB `unrar` as the sole supported external decompressor for
RAR member data. It MUST identify the binary on `PATH` as RARLAB `unrar` before use and
MUST NOT implement a fallback matrix to `unrar-free`, `unar`, `bsdtar`, `7z`, or other
tools when RARLAB `unrar` is missing or incompatible.

#### Scenario: single-tool matrix

| Case | Expected |
| --- | --- |
| RARLAB `unrar` on `PATH` | Used for compressed/encrypted member data |
| Only `unrar-free` / `unar` / `7z` on `PATH` | `PackageNotInstalledError` naming RARLAB `unrar`; no silent fallback |
| Listing without data reads | Succeeds without invoking any external decompressor |

### Requirement: Optional extras map only to libraries the code uses

User-facing extras SHALL list only libraries imported by `src/` at runtime for that
capability. A package used only by tests, decode oracles, fixture generation, or
fuzz harnesses MUST live in a PEP 735 dependency group (`dev`, `fuzz`, …) and be
absent from every user-facing extra.

The per-codec library choice and rationale SHALL be recorded in
`docs/internal/library-analysis.md`. A guard test or check script SHALL prevent dead or
test-only dependencies from returning to user-facing extras. A dependency pinned
ahead of its implementation phase, such as `[cli]` or `[7z-write]`, is permitted only
through an explicit documented allowlist in that guard.

#### Scenario: dependency-audit matrix

| Case | Expected |
| --- | --- |
| User-facing extra audited against `src/` imports | Every pinned package is reachable from runtime code or explicitly allowlisted |
| Library imported only by tests (`rarfile`, oracle `py7zr`, `ncompress`, fixture-only `pyzstd`) | Declared in `dev`; absent from runtime extras |
| `atheris` | Declared in `fuzz` group; absent from runtime extras and `[all]` |
| `pip install archivey[zstd]` on Python 3.11-3.13 | Installs `backports.zstd`; does not pull `zstandard` |
| `pip install archivey[zstd]` on Python 3.14+ | No third-party zstd package required; stdlib `compression.zstd` provides the backend |
| Extra lists a library no `src/` module imports and not allowlisted | Packaging audit fails |

### Requirement: CI-only fuzz dependency group

The system SHALL provide a PEP 735 dependency group named `fuzz` that installs
`atheris` (and any harness-only helpers it needs) for coverage-guided fuzz CI.
The `fuzz` group is **not** a user-facing runtime extra: it MUST NOT appear in
`[all]`, `[recommended]`, `[recommended-lite]`, or any format/codec/CLI extra,
and MUST NOT be required to import or use `archivey` at runtime.

#### Scenario: fuzz packaging matrix

| Case | Expected |
| --- | --- |
| `pip install archivey` / `archivey[all]` | `atheris` not installed |
| Fuzz CI job | Installs via `uv sync --group fuzz` (plus target runtime needs) |
| Runtime import of `archivey` without fuzz group | No `atheris` dependency |

### Requirement: Supported runtime environment

The system SHALL declare and support Python 3.11 or newer on Linux, macOS, and
Windows. The public API remains synchronous.

Readers and writers are not generally thread-safe. The supported
`MemberStreams.CONCURRENT` contract — what concurrent opens, materialization,
passes, close/lifecycle, and same-stream rules mean — lives entirely in
`reader-concurrency` (default single-live-stream rule: `archive-reading`). This
capability SHALL NOT restate that contract.

When that contract is declared, behavior SHALL be data-race-free on regular
CPython and on the backend/runtime combinations exercised by the required Linux
CPython `3.13t` `free-threaded-concurrency` CI job. It MUST NOT depend on
incidental GIL serialization. Optional backends without a free-threaded-compatible
wheel are not claimed covered until an equivalent dedicated job executes them.
This is a packaging/CI correctness claim, not a parallel-speed guarantee. Writers
remain not thread-safe.

#### Scenario: runtime-support matrix

| Case | Expected |
| --- | --- |
| Install on Linux, macOS, or Windows under Python 3.11+ | Core and installed optional formats are supported |
| Install on Python older than 3.11 | `requires-python >=3.11` prevents installation |
| Required `3.13t` core-backend job runs concurrent reader tests | Same bytes/lifecycle behavior as regular CPython for covered backends |
| Optional backend unavailable in `3.13t` job | Ordinary-build coverage remains valid; free-threaded support is not claimed for that backend |
| Public docs for `MemberStreams.CONCURRENT` | Point at the supported contract without labeling it provisional |

### Requirement: Version metadata exposure

The system SHALL expose the installed version as `archivey.__version__`, resolved
from installed distribution metadata via `importlib.metadata` rather than a
hard-coded string.

#### Scenario: installed-version metadata

| Case | Expected |
| --- | --- |
| Caller reads `archivey.__version__` | Returns the version recorded in installed package metadata, e.g. `"0.2.0"` |

### Requirement: Source package layout separates public API from implementation

The installable `archivey` package SHALL keep the supported public API at the
package root. Only public API modules appear in `archivey.__all__`: `core.py`,
`types.py`, `exceptions.py`, `cost.py`, and `reader.py`. `archivey.__init__.py`
SHALL re-export the public API so supported callers do not import from
`archivey.internal.*`.

Implementation code SHALL live under `archivey.internal.*` without public
stability guarantees. Format backends SHALL live under
`archivey.internal.backends.*` and register with the registry at import time.
Importing top-level `archivey` SHALL still register all bundled backends. The
codec/stream layer SHALL remain under `archivey.internal.streams.*`. Phase 4
extraction modules SHALL follow the same implementation-under-`internal` rule while
public extraction types and `extract()` live on the public surface.

#### Scenario: package-layout matrix

| Case | Expected |
| --- | --- |
| Application uses documented API (`open_archive`, `ArchiveMember`, etc.) | `import archivey` or public re-exports suffice; no `archivey.internal` import required |
| Caller imports `archivey.internal.backends.zip` or old `archivey.formats.zip_reader` | Not documented, not in `__all__`, and not a stability promise |
| `import archivey` in a core-only environment | `list_supported_formats()` returns bundled formats without a prior `open_archive()` call |
