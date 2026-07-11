## MODIFIED Requirements

### Requirement: Supported Runtime Environment

The system SHALL declare and support Python 3.11 or newer on Linux, macOS, and
Windows. The public API remains synchronous.

Readers and writers are not generally thread-safe, but the reader contract has one
explicit supported concurrency seam, available on readers opened with
`MemberStreams.CONCURRENT`: after such a reader's member list has been fully
materialized and published, workers MAY concurrently call `open()` and independently
`read`/`readinto`/`close` different returned member streams, plus `seek`/`tell` under
`MemberStreams.SEEKABLE` when the individual stream supports positioning. Without the
declared capability, one member stream may be live at a time on every format. Iteration,
materialization, `stream_members`, extraction coordination, and reader `close` remain
single-owner operations and cannot execute concurrently with active calls in that seam. An idle
open member stream may outlive a non-concurrent reader close under the lifecycle-lease
contract. Single-owner composition uses explicit private child scopes, so extraction may
drive its own streaming pass/yielded-stream I/O without admitting unrelated public reentry.
Writers remain not thread-safe.

`MemberStreams.CONCURRENT` is a **supported** opt-in capability (no longer provisional):
the seam is correct under cooperative use and is exercised on free-threaded CPython by
the required Linux CI job below.

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

- **WHEN** post-materialization concurrent member opens and independent stream operations
  run in the required CPython `3.13t` core-backend CI job
- **THEN** they produce the same correct bytes/lifecycle behavior as on a regular build,
  without cache/password/source-position data races

#### Scenario: unavailable optional wheel does not imply untested support

- **WHEN** an optional backend cannot be installed in the `3.13t` job
- **THEN** its ordinary-build coverage remains valid, but free-threaded support is not claimed
  for that backend until a dedicated job runs it

#### Scenario: general reader mutation is not made thread-safe

- **WHEN** a caller attempts to overlap iteration, materialization, extraction, or reader
  close with worker member-stream operations
- **THEN** that schedule is outside the supported seam and the later public operation is
  rejected as a usage error

#### Scenario: CONCURRENT is documented as supported

- **WHEN** a caller reads the public `MemberStreams.CONCURRENT` documentation
- **THEN** it describes the supported cooperative + free-threaded-tested seam without
  labeling the capability as provisional
