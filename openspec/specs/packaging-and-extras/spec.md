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
| `[7z]` | `pyppmd`, `inflate64`, `backports.zstd` (<3.14), `brotli`, `cryptography` | **all** 7z reading features — PPMd, Deflate64, Zstd, Brotli, and AES-encrypted 7z |
| `[rar]` | `cryptography`, a Blake2sp backend | **all** RAR reading features that need a Python package — header-encrypted RAR5 and Blake2sp checksum verification (member *data* still needs the `unrar` binary) |
| `[crypto]` | `cryptography` | the AES/crypto backend alone — a subset of `[7z]`/`[rar]`, for callers who want only encryption support |
| `[7z-write]` | `py7zr` | 7-Zip **writing** (reading is native, no extra) |
| `[iso]` | `pycdlib` | ISO 9660 (`.iso`) |
| `[zstd]` | `backports.zstd` (<3.14 only) | standalone Zstandard (`.zst`, `.tar.zst`); on Python 3.14+ the stdlib `compression.zstd` is used with no extra |
| `[lz4]` | `lz4` | LZ4 (`.tar.lz4`) |
| `[unix-compress]` | `uncompresspy` | unix-compress (`.Z`, `.tar.Z`) — LZW decompression |
| `[cli]` | `tqdm` | the `archivey` command-line interface and its progress bar |
| `[seekable]` | `rapidgzip` | faster gzip/bzip2 decompression **and random access (seeking)** into gz/bz2 streams. rapidgzip backs both codecs (bzip2 via its bundled `IndexedBzip2File`); the standalone `indexed_bzip2` package is deliberately not used (loading both corrupts the heap on macOS). Native C++ lib: install needs a prebuilt wheel, or a C++17 compiler where no wheel exists |
| `[recommended-lite]` | `[7z]` + `[rar]` + `[7z-write]` + `[iso]` + `[zstd]` + `[lz4]` + `[unix-compress]` + `[cli]` | every format/codec with broadly-wheeled deps; **no build-finicky C++ libs**. Use when `[recommended]` won't install |
| `[recommended]` | `[recommended-lite]` + `[seekable]` | the **recommended** install — everything in `[recommended-lite]` plus gz/bz2 seeking and speed |
| `[all]` | `[recommended]` **plus** every alternative/secondary backend (currently none) | everything, including any redundant alternative backends — mainly for testing/benchmarking |

`[recommended]` is the sensible "give me everything useful" install: every
format/codec with one primary backend each, plus the `[seekable]` backends that add
gz/bz2 random access and speed. Almost all of these dependencies are native
extensions, but the bundled ones ship broad manylinux/musllinux/macOS/Windows wheels
and install without a compiler on mainstream platforms — **except** `rapidgzip`
(the `[seekable]` lib), which more often lacks a wheel and falls back
to a C++17 source build. `[recommended-lite]` is therefore `[recommended]` minus only
that one: it keeps every format and codec (`cryptography`, `pyppmd`, `backports.zstd`,
`lz4`, …) and just drops gz/bz2 seeking, so it installs reliably where the C++ build
fails. A user who hits a build error on `[recommended]` can fall back to
`[recommended-lite]` without losing any format support.

`[all]` is a superset of `[recommended]` that additionally pulls any **alternative**
backends — performance or compatibility variants that duplicate a capability already
covered. **At present there are none**, so `[all]` currently resolves to exactly
`[recommended]`: the two alternatives that used to live here (`python-xz` as an alternative
xz backend, and `pyzstd` as a second zstd library) were dropped by the compression-library
evaluation (`docs/library-analysis.md`) — v2 reads XZ with its own native parser
(`davitf/archivey-dev#214`) and uses the stdlib zstd line, so neither alternative is
imported by `src/`. The `[all]` alias is kept so a future alternative backend can be
re-added behind it without churn; **most users should install `[recommended]` (or
`[recommended-lite]`), not `[all]`**.

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

Development-only tooling (test / lint / type-check), the test-oracle libraries
(`py7zr`, `rarfile`), and the fixture-generator libraries (`ncompress`, an LZW
*compressor* used to produce `.Z` fixtures) are NOT runtime extras. They are declared as a
PEP 735 `[dependency-groups]` entry (`dev`), so they are never installed for end users and
are pulled in by `uv sync` (or `pip install --group dev`) for contributors.

#### Scenario: installing an extra enables exactly its capability

- **WHEN** `pip install archivey[iso]` is run
- **THEN** `pycdlib` is installed and `.iso` archives become available
- **AND** no other optional dependency (py7zr, backports.zstd, lz4) is pulled in

#### Scenario: `[recommended]` enables every capability plus gz/bz2 seeking

- **WHEN** `pip install archivey[recommended]` is run
- **THEN** every optional capability (7z PPMd/Deflate64/Zstd/Brotli/AES, encrypted RAR5 + Blake2sp verification, 7z write, ISO, ZST, LZ4, CLI) is available with one primary backend per capability
- **AND** the `[seekable]` backend (`rapidgzip`, which also provides bzip2 via its bundled `IndexedBzip2File`) is installed, enabling gz/bz2 random access and faster decompression
- **AND** no redundant alternative backend (e.g. a second xz/zstd implementation) is pulled in

#### Scenario: `[recommended-lite]` keeps every format but drops the C++ seek libs

- **WHEN** `pip install archivey[recommended-lite]` is run (e.g. because `[recommended]` failed to build `rapidgzip`)
- **THEN** every format and codec still works (7z, RAR, ISO, ZST, LZ4, crypto, …) with no native-build risk
- **AND** the only lost capability is gz/bz2 seeking and the speed boost from the `[seekable]` libs

#### Scenario: `[all]` additionally pulls alternative backends

- **WHEN** `pip install archivey[all]` is run
- **THEN** everything in `[recommended]` is installed **plus** any alternative/secondary backends that add no new capability — of which there are currently none, so `[all]` resolves to exactly `[recommended]` (the dropped `python-xz` / `pyzstd` alternatives are no longer pinned; see `docs/library-analysis.md`)

#### Scenario: RAR reading requires only the system unrar binary

- **WHEN** a core install is used to read RAR data but no `unrar` binary is on PATH
- **THEN** RAR listing still works (native metadata), but RAR data reads fail with a clear error indicating the external `unrar` tool is required — no pip extra would fix this

---

### Requirement: Optional extras map to exactly the libraries the code uses

User-facing optional **extras** (`[7z]`, `[zstd]`, `[all]`, …) SHALL list only libraries
that `src/` imports at runtime for that capability; an extra MUST NOT pin a dependency that
no `src/` code path uses. Libraries needed **only by the test suite** — decode oracles
(`rarfile`, `py7zr`) and fixture generators (`ncompress`, and `pyzstd` while it is only used
to *write* zstd fixtures) — SHALL live in the `dev` dependency group, never in a user-facing
extra. The per-codec library choice and its rationale SHALL be recorded in
`docs/library-analysis.md`, the source of truth for why each library is used or rejected.

A guard (a unit test or check script) SHALL enforce this so a dead or test-only dependency
cannot slip back into an extra. A library pinned in an extra **ahead of** its
implementation phase (e.g. `tqdm` for `[cli]`, `py7zr` for `[7z-write]`) is permitted only
via an explicit, documented allowlist in that guard.

#### Scenario: no dead optional dependency in a user-facing extra

- **WHEN** the `[all]` extra (or any user-facing extra) is audited against `src/` imports
- **THEN** every pinned package is reachable from some `src/` code path (or an explicitly allowlisted not-yet-implemented capability), or it is removed

#### Scenario: a test-only library lives in the dev group, not an extra

- **WHEN** a library is imported only by the test suite (an oracle or a fixture generator), e.g. `rarfile`, `py7zr`, `ncompress`, or fixture-only `pyzstd`
- **THEN** it is declared in the `dev` dependency group and is absent from every user-facing extra

#### Scenario: the zstd extra pins the stdlib-line backend

- **WHEN** `pip install archivey[zstd]` is run on Python 3.11–3.13
- **THEN** `backports.zstd` is installed and `.zst` / `.tar.zst` reading works via the `compression.zstd` API
- **AND** `zstandard` is not pulled in

#### Scenario: no zstd runtime dependency on Python 3.14+

- **WHEN** `pip install archivey[zstd]` is resolved on Python 3.14 or newer
- **THEN** no third-party zstd package is required, because the standard-library `compression.zstd` provides the backend

---

### Requirement: Supported Runtime Environment

The system SHALL declare and support Python 3.11 or newer on Linux, macOS, and
Windows. The public API remains synchronous.

Readers and writers are not generally thread-safe, but the reader contract has one
explicit supported concurrency seam, available on readers opened with
`MemberStreams.CONCURRENT`: concurrent first-touch materialization is coordinated
(exactly one build; overlapping `open()` / `members()` / `get()` wait for the published
snapshot rather than rejecting), after which workers MAY concurrently call `open()` and
independently `read`/`readinto`/`close` different returned member streams, plus
`seek`/`tell` under `MemberStreams.SEEKABLE` when the individual stream supports
positioning. `reader.close()` drains in-flight worker calls then closes; escaped idle
member streams remain governed by the lifecycle-lease contract. Without the declared
capability, one member stream may be live at a time on every format. Distinct
reader-wide passes (`__iter__`, `stream_members`, `extract_all`) remain single-owner
and cannot execute concurrently with each other or with active worker calls. Same-stream
concurrent access stays the caller's responsibility. Single-owner composition uses
explicit private child scopes, so extraction may drive its own streaming pass/yielded-stream
I/O without admitting unrelated public reentry.
Writers remain not thread-safe.

`MemberStreams.CONCURRENT` is a **supported** opt-in capability: the seam is correct under
cooperative use and is exercised on free-threaded CPython by the required Linux CI job
below.

The supported reader seam SHALL be data-race-free on regular CPython and on the
backend/runtime combinations exercised by the required Linux CPython `3.13t`
`free-threaded-concurrency` CI job. It MUST NOT depend on incidental GIL serialization.
An optional backend without a free-threaded-compatible wheel is not claimed covered until an
equivalent dedicated job can execute it. This is a correctness contract only: optional
packages, source-handle locking, codec behavior, and archive layout may serialize execution,
and Archivey makes no parallel-speed guarantee.

#### Scenario: supported on all three operating systems

- **WHEN** the library is installed on Linux, macOS, or Windows under Python 3.11+
- **THEN** the core and any installed optional formats are supported on that platform

#### Scenario: install rejected on unsupported Python

- **WHEN** installation is attempted on Python older than 3.11
- **THEN** the package's `requires-python >=3.11` declaration prevents installation

#### Scenario: tested free-threaded build preserves the narrow reader contract

- **WHEN** concurrent first-touch materialization, post-publication member opens, and
  independent stream operations run in the required CPython `3.13t` core-backend CI job
- **THEN** they produce the same correct bytes/lifecycle behavior as on a regular build,
  without cache/password/source-position data races

#### Scenario: unavailable optional wheel does not imply untested support

- **WHEN** an optional backend cannot be installed in the `3.13t` job
- **THEN** its ordinary-build coverage remains valid, but free-threaded support is not claimed
  for that backend until a dedicated job runs it

#### Scenario: distinct passes and shared streams remain single-owner

- **WHEN** a caller overlaps distinct reader-wide passes (`__iter__`, `stream_members`,
  `extract_all`) or concurrently accesses one stream object
- **THEN** overlapping passes are rejected as a usage error, and same-stream correctness
  remains the caller's responsibility under standard file semantics

#### Scenario: CONCURRENT is documented as supported

- **WHEN** a caller reads the public `MemberStreams.CONCURRENT` documentation
- **THEN** it describes the supported cooperative + free-threaded-tested seam without
  labeling the capability as provisional

---

### Requirement: Version Metadata Exposure

The system SHALL expose its installed version as `archivey.__version__`, resolved
from the installed distribution metadata via `importlib.metadata` rather than a
hard-coded string.

#### Scenario: __version__ reflects the installed distribution

- **WHEN** a caller reads `archivey.__version__`
- **THEN** it returns the version recorded in the installed package metadata (e.g. `"0.2.0"`)

---

### Requirement: Source package layout separates public API from implementation

The installable `archivey` package SHALL organize modules so that:

1. **Public API modules** live at the package root and are the only modules whose symbols
   appear in `archivey.__all__`: `core.py` (entry points and registry queries),
   `types.py` (data model), `exceptions.py` (error hierarchy), `cost.py` (`CostReceipt`
   and related enums), and `reader.py` (the public `ArchiveReader` ABC).
2. **`archivey.__init__.py`** SHALL re-export the public API and SHALL NOT require callers
   to import from `archivey.internal.*` for supported usage.
3. **Implementation code** SHALL live under `archivey.internal.*`, which is not a supported
   import surface for external callers and carries no backwards-compatibility guarantee.
4. **Format backends** SHALL live under `archivey.internal.backends.*` (not at the package
   root). Backend modules register with the registry at import time; importing the top-level
   `archivey` package SHALL still register all bundled backends.
5. **The codec/stream layer** SHALL remain under `archivey.internal.streams.*`.

Phase 4 modules (`internal/extraction.py`, `internal/filters.py`) follow the same rule:
implementation under `internal/`, public extraction types and `extract()` on `core.py` /
`types.py`.

#### Scenario: supported usage imports only the top-level package

- **WHEN** application code uses the documented API (`open_archive`, `ArchiveMember`, …)
- **THEN** `import archivey` (or explicit imports from `archivey` re-exports) suffices
- **AND** no import from `archivey.internal` is required

#### Scenario: backends are not a public subpackage

- **WHEN** a caller attempts `import archivey.internal.backends.zip` (or the old
  `archivey.formats.zip_reader`)
- **THEN** that path is not documented, not in `__all__`, and not a stability promise
  (it may work for debugging but is not part of the public contract)

#### Scenario: import archivey registers bundled backends

- **WHEN** `import archivey` is executed in a core-only environment
- **THEN** `list_supported_formats()` returns the bundled format set without a prior
  `open_archive()` call
