# Testing Contract — delta (concurrent-member-streams)

## ADDED Requirements

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
MUST vary operation interleavings and assert exact bytes/state, not merely lack of exceptions.

CI SHALL define a required Linux `free-threaded-concurrency` job that installs CPython
`3.13t`, uses the zero-dependency core environment, and runs tests marked
`concurrent_reader`. The marker SHALL cover directory, ZIP, single-file stdlib codecs,
SharedSource, lifecycle/operation state, and TAR. The job MUST fail rather than skip merely
because the GIL is disabled. An optional backend unavailable on `3.13t` is excluded from the
free-threaded support claim until an equivalent dedicated job runs it.

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

#### Scenario: baseline measurement has no arbitrary threshold

- **WHEN** the TAR/ISO correctness lock is implemented
- **THEN** its practical serialization metrics are recorded without blocking correctness on a
  speed target

#### Scenario: performance claim has proportionate evidence

- **WHEN** a later change claims faster independent handles, shared decoding, or lock removal
- **THEN** it supplies focused before/after metrics for the resources that mechanism changes
