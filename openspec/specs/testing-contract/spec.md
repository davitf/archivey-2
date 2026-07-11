# Testing Contract

## Purpose

The test suite must verify that all supported formats produce uniform, interchangeable `ArchiveMember` objects; that adversarial archives are rejected safely; that every writable format round-trips without data loss; and that every streaming backend operates correctly on non-seekable inputs.

## Requirements

### Requirement: Equivalence matrix across formats

The system SHALL produce identical `ArchiveMember` objects from ZIP, TAR, 7z, RAR, and ISO sources when reading a canonical directory structure (files, symlinks, nested directories, empty directories, filenames with unicode and spaces). Equivalence is defined as field-by-field equality excluding the identity fields (`member_id`/`archive_id`), `raw_name`, `compressed_size`, `hashes`, and `extra`. Format-specific limitation flags (`ArchiveFormatFeatures`) encode per-format expected deviations and are used by the assertion helper to limit the comparison to the fields each format can faithfully represent.

#### Scenario: same canonical structure, multiple formats

- **WHEN** the same canonical directory structure is archived into ZIP, TAR, 7z, RAR, and ISO
- **THEN** the `ArchiveMember` objects produced by reading each archive are equal on all fields except the identity fields (`member_id`/`archive_id`), `raw_name`, `compressed_size`, `hashes`, and `extra`
- **AND** any per-format field limitations are captured in `ArchiveFormatFeatures` flags rather than silently excluded from the comparison

### Requirement: Adversarial corpus coverage

The system SHALL include an adversarial test corpus that exercises every documented attack category and verifies that the correct exception is raised or limit is enforced in each case. The required adversarial cases are:

| Case | Expected outcome |
|---|---|
| Zip bomb — quine-style and nested (42.zip variant) | `max_ratio` and `max_extracted_bytes` limits enforced |
| Ratio-floor false positive — tiny highly-compressible file (10 B → 15 KiB, 1500:1) | Extracts **without** error; output stays under `ratio_activation_threshold` |
| Path traversal — `../evil`, `../../etc/passwd`, `./../../outside` | `PathTraversalError` raised |
| Absolute paths — `/etc/passwd`, `C:\Windows\System32\evil.dll` | `PathTraversalError` raised |
| Symlink escape — symlink pointing to `../../outside`, and chained symlinks | `SymlinkEscapeError` raised |
| Symlink loop — cyclic symlinks (`a → b`, `b → a`) | `SymlinkEscapeError` raised; no uncaught `OSError`/crash |
| Corrupt archive — truncated ZIP (missing EOCD), truncated TAR, bad CRC | `CorruptionError` or `TruncatedError` raised |
| Unicode bombs — `\x00` in paths, RTL override characters in filenames | `PathTraversalError` raised (for null bytes); warning or rejection for RTL |
| Giant claimed size — member claims 1 TiB uncompressed but archive is 1 KiB | Extraction aborts cleanly before exhausting resources |

Adversarial archives are committed as binary fixtures under `tests/fixtures/adversarial/`. Regenerable fixtures are produced by `tests/create_adversarial.py`.

#### Scenario: zip bomb extraction

- **WHEN** a zip bomb archive is extracted with default limits
- **THEN** extraction raises `ExtractionError` before the `max_extracted_bytes` or `max_ratio` threshold is exceeded

#### Scenario: path traversal member

- **WHEN** an archive containing a member named `../evil` is extracted
- **THEN** extraction raises `PathTraversalError` and no file is written outside the destination

#### Scenario: corrupt archive

- **WHEN** an archive with a truncated or CRC-invalid member is read
- **THEN** `CorruptionError` or `TruncatedError` is raised with the original exception attached as `__cause__`

### Requirement: Round-trip test for every writable format

The system SHALL include a round-trip test for every writable format. The test sequence is `create → extract → compare` and must produce identical files and metadata within the format's documented timestamp and permission limitations.

#### Scenario: ZIP round-trip

- **WHEN** a canonical file set is written to a ZIP archive and then extracted
- **THEN** the extracted files match the originals in content and in all metadata fields the ZIP format can faithfully represent

#### Scenario: TAR round-trip

- **WHEN** a canonical file set is written to a TAR archive and then extracted
- **THEN** the extracted files match the originals in content and in all metadata fields the TAR format can faithfully represent

### Requirement: Cross-validate native readers against reference oracles

The system SHALL validate the native 7-Zip and RAR readers against reference
implementations used purely as test oracles: `py7zr` and the `7z` CLI for 7-Zip,
and `rarfile` and the `unrar` CLI for RAR. For a representative corpus of
archives, the native reader's member metadata and decompressed bytes MUST match
the oracle's. These oracle libraries are `dev`-group dependencies only and are
never required at runtime; oracle-backed tests SHALL be skipped (not failed) when
the oracle library or CLI tool is unavailable in the environment.

The corpus MUST exercise the core codecs the native 7z reader supports without
extras (LZMA1, LZMA2, simple BCJ filters, Delta, BZip2, Deflate, STORED) and —
when the relevant extras are installed — PPMd / Deflate64 (`[7z]`) and
AES-encrypted archives (`[crypto]`). It MUST assert that genuinely unsupported
codecs (BCJ2, and unrecognized method IDs) raise the documented "unsupported
codec" error rather than diverging silently from the oracle.

#### Scenario: native 7z reader matches the py7zr oracle

- **WHEN** a 7-Zip archive in the corpus is read by both the native reader and `py7zr`
- **THEN** member metadata and decompressed bytes are identical between the two
- **AND** the test is skipped (not failed) if `py7zr` is not installed

#### Scenario: native RAR reader matches the rarfile/unrar oracle

- **WHEN** a RAR archive in the corpus is read by both the native reader and `rarfile`/`unrar`
- **THEN** member metadata and decompressed bytes are identical between the two
- **AND** the test is skipped if `rarfile` or the `unrar` binary is unavailable

#### Scenario: unsupported 7z codec is rejected, not guessed

- **WHEN** a 7-Zip archive using BCJ2 (or an unrecognized method ID) is read by the native reader
- **THEN** the documented unsupported-codec error is raised, rather than returning bytes that disagree with the oracle

### Requirement: Non-seekable stream coverage for every streaming backend

The system SHALL test every backend that supports streaming with a `FakeNonSeekable` wrapper that raises `io.UnsupportedOperation` on all `seek` and `tell` calls. The test verifies that the backend reads and iterates correctly when the source stream cannot be repositioned. A backend that **requires** a seekable source (ZIP, ISO — see `access-mode-and-cost` and the format specs) SHALL instead be tested to **fail fast** at open time with `StreamNotSeekableError`, never buffering the stream implicitly.

#### Scenario: non-seekable ZIP source fails fast

- **WHEN** a ZIP archive is opened through a `FakeNonSeekable` wrapper
- **THEN** `open_archive` raises `StreamNotSeekableError` at open time (ZIP requires a seekable source; the library never implicitly buffers), and no member data is read

#### Scenario: non-seekable TAR.GZ source

- **WHEN** a `.tar.gz` archive is opened through a `FakeNonSeekable` wrapper
- **THEN** all members are iterable and their data is readable without error

### Requirement: Corpus conformance sweep

The test suite SHALL include a single parametrized conformance sweep driven by the
declarative archive corpus: every corpus entry whose format is currently implemented
MUST open via `open_archive()`, list members matching the entry's declared expected
contents (names, types, sizes, link targets), and extract cleanly to a temporary
directory under the default safety policy with contents verified — or, for an entry that
declares an expected failure (encrypted without a password, unsupported variant,
adversarial member), raise exactly the documented `ArchiveyError` subclass. Corpus
entries for formats that are not yet implemented (7z/RAR before Phase 6) SHALL be
carried in the corpus but skipped by the sweep via a registry-driven guard, so enabling
a format activates its entries without re-porting. Entries needing an absent optional
dependency SHALL skip, not fail.

The corpus SHALL cover at least the archive shapes present in the DEV declarative corpus
for the implemented formats (multi-member trees, unicode and non-UTF-8 names, symlinks/
hardlinks, duplicate names, empty archives and empty members, per-format metadata
quirks), and the corpus module SHALL record the DEV commit hash the shapes were ported
from.

#### Scenario: corpus archive round-trips through the sweep

- **WHEN** the sweep runs a corpus entry for an implemented format
- **THEN** the archive opens, its member listing matches the declared expectations, and extraction to a temp directory succeeds with verified contents

#### Scenario: corpus entry with a documented failure

- **WHEN** the sweep runs a corpus entry declared to fail (e.g. encrypted, no password)
- **THEN** the documented `ArchiveyError` subclass is raised and the sweep passes

#### Scenario: unimplemented-format entries are skipped, then activate

- **WHEN** the sweep encounters a 7z or RAR corpus entry before the native readers exist
- **THEN** the entry is skipped via the registry-driven guard (and runs once the format's reader registers)

### Requirement: Frozen DEV oracle retired

The frozen DEV oracle tree (`tests/_dev_oracle/`) SHALL NOT exist: its durable assets —
the declarative corpus shapes (ported into the v2 corpus) and the oracle libraries
(py7zr/rarfile and the `7z`/`unrar` CLIs, which remain dev-group cross-validation
oracles per the cross-validation requirement) — are preserved elsewhere, and the dead
v1-API test drivers are deleted rather than maintained. Tooling configuration SHALL
carry no special-case exclusions for the oracle tree.

#### Scenario: no oracle tree or exclusions remain

- **WHEN** the repository is searched for `_dev_oracle`
- **THEN** no test tree and no pytest/ruff/type-checker exclusion entries reference it

### Requirement: Capability-gate behavior is tested on every format

The test suite SHALL cover the declared-capabilities gate uniformly:

- For every implemented format — directory included — a reader opened without
  `MemberStreams.CONCURRENT` raises `ConcurrentAccessError` on a second overlapping
  `open()` while the first stream stays readable, and the sequential
  `open → read → close → open next` loop succeeds without any declaration.
- The `ConcurrentAccessError` message includes the recorded `open_archive()` call site.
- Without `MemberStreams.SEEKABLE`, member streams (random `open()` and
  `stream_members()` yields) report `seekable() is False` and `seek()` raises
  `io.UnsupportedOperation` on every format, including directory members backed by real
  files; with it, positioning works where the backend provides it.
- `extract_all()` — including hardlink recovery and symlink-target reads — succeeds on
  readers with no declared capabilities.
- `ArchiveyUsageError`s (and `ConcurrentAccessError`) are NOT `ArchiveyError` subclasses:
  a test asserts a blanket `except ArchiveyError` does not catch them.
- Accelerator/index activation is demand-driven: an undeclared reader over an
  accelerator-eligible source instantiates no seek index; a declared-`SEEKABLE` one
  resolves `AUTO` accelerators as specified in `seekable-decompressor-streams`.

#### Scenario: gate fires uniformly across formats

- **WHEN** the gate matrix runs a second overlapping `open()` for each implemented format
  without declared `CONCURRENT`
- **THEN** every format raises `ConcurrentAccessError` naming the open site, and the
  first stream remains readable

#### Scenario: usage errors escape ArchiveyError handlers

- **WHEN** a `ConcurrentAccessError` is raised inside a `try/except ArchiveyError` block
- **THEN** the exception propagates out of that block

### Requirement: Concurrent member-stream correctness and free-threaded stress

The test suite SHALL exercise the supported post-materialization concurrency seam
(readers declared with `MemberStreams.CONCURRENT`) across
representative backend shapes: independent file handles (directory), library-coordinated
handles (ZIP), archivey SharedSource views (single-file and native 7z/RAR as available),
and archivey-locked library handles (random-access TAR and ISO).

Tests SHALL cover concurrent `open()` by member and by name; independent stream
`read`/`readinto`/`close` plus supported positioning; standard `io.UnsupportedOperation` for
non-seekable streams; cache publication separate from lifecycle; operation-owner child
scopes; generator abandonment; lifecycle leases/failure/finalizers/caller-owned sources;
password candidate/provider coordination; and detected unsupported overlap. Stress tests
MUST vary operation interleavings **across threads** and assert exact bytes/state, not
merely lack of exceptions.

CI SHALL define a required Linux `free-threaded-concurrency` job that installs CPython
`3.13t`, uses the zero-dependency core environment, and runs tests marked
`concurrent_reader`. The marker SHALL cover directory, ZIP, single-file stdlib codecs,
SharedSource, lifecycle/operation state, and TAR. The job MUST fail rather than skip merely
because the GIL is disabled. An optional backend unavailable on `3.13t` is excluded from the
free-threaded support claim until an equivalent dedicated job runs it. ISO multi-thread
coverage runs in the ordinary `[all]` matrix (optional `pycdlib`); it is not claimed under
the core-only `3.13t` job until a dedicated extras job exists.

The TAR/ISO correctness-lock implementation SHALL record a proportionate baseline: wall time
and lock wait/hold time, plus seek count and bytes decompressed/read where practical. There is
no pass/fail performance threshold and the measurement is not a correctness merge gate.
A later optimization or speed claim SHALL include targeted before/after measurements for the
mechanism it changes; peak memory and broader DIRECT/SOLID workloads are required only when
that strategy can affect buffering/materialization or decompression work.

#### Scenario: backend-shape concurrency matrix

- **WHEN** each available representative backend is materialized and workers
  open/read/use-supported-positioning/close distinct member streams under varied interleavings
- **THEN** every worker receives exact member bytes and independent positions for supported
  positioning, while non-seekable streams retain standard unsupported-operation behavior

#### Scenario: required free-threaded stress job

- **WHEN** the required `free-threaded-concurrency` job runs marked tests under CPython
  `3.13t`
- **THEN** they pass without cache, lifecycle, password, or source-position data races

#### Scenario: multi-thread stress covers core backends

- **WHEN** multi-thread workers concurrently open and read distinct members after
  materialization on directory, ZIP, stdlib single-file, SharedSource, and plain TAR
- **THEN** each worker observes exact member bytes and documented misuse still raises
  usage/concurrent-access errors

#### Scenario: baseline measurement has no arbitrary threshold

- **WHEN** the TAR/ISO correctness lock is implemented
- **THEN** its practical serialization metrics are recorded without blocking correctness on a
  speed target

#### Scenario: performance claim has proportionate evidence

- **WHEN** a later change claims faster independent handles, shared decoding, or lock removal
- **THEN** it supplies focused before/after metrics for the resources that mechanism changes
