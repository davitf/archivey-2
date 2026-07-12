## MODIFIED Requirements

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
