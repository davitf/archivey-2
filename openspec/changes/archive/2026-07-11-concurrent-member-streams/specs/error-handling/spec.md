# Error Handling — delta (concurrent-member-streams)

## ADDED Requirements

### Requirement: Usage errors are a separate hierarchy from ArchiveyError

The system SHALL define `ArchiveyUsageError(Exception)` — deliberately **not** an
`ArchiveyError` subclass — as the root for errors that indicate a bug in the calling
code rather than a property of the archive, the environment, or a supported limitation.
`except ArchiveyError` is what applications wrap archive handling in; it MUST NOT
swallow caller misuse.

`ConcurrentAccessError(ArchiveyUsageError)` SHALL be raised when a second member stream
is opened while another is live on a reader without `MemberStreams.CONCURRENT`. Its
message SHALL include the recorded `open_archive()` call site (`file:line`) so the error
points at where the capability should have been declared.

`ArchiveyUsageError` (the root, or a future subclass) SHALL cover the other detected
misuse states:

- a reader-wide single-owner operation overlapping materialization/iteration/streaming/
  extraction;
- a reader close overlapping an actively executing member-worker call (an idle leased
  stream is not overlap);
- any new reader operation/property except repeated `close()` / `__exit__` after
  `reader.close()`;
- same-reader password-provider reentry into a password-requiring operation that would
  deadlock;
- opening/using an `ArchiveMember` that does not belong to this reader (wrong-reader
  identity); and
- member I/O after the caller closed its own supplied source early.

The error SHALL be raised at the later operation before it changes state and MUST leave
the earlier operation/stream usable.

`open_archive(streaming=True, member_streams=…CONCURRENT…)` SHALL raise
`ArchiveyUsageError` (invalid access-mode/capability combination).

Boundaries of the hierarchy:

- `UnsupportedOperationError` and `UnsupportedFeatureError` remain `ArchiveyError`s:
  they describe what an archive, format, backend, or access mode cannot provide — an
  input/environment property, not a caller bug.
- Stream-level conventions stay stdlib-shaped and are not archivey taxonomy: I/O on a
  **closed stream** raises `ValueError`; unsupported positioning raises
  `io.UnsupportedOperation` (this is also how undeclared `SEEKABLE` surfaces, because
  seek-probing consumers already check `seekable()`/catch that type).

Internal operation-owner children are not overlap: materialization/worker link reads,
`extract_all()` member/counter peeks and owned `stream_members()` passes, and I/O/close
on a pass's yielded stream carry the root token explicitly. Reentrant public calls do
not inherit that token implicitly and remain rejectable.

#### Scenario: usage errors are not caught by ArchiveyError handlers

- **WHEN** application code wraps archive handling in `except ArchiveyError` and a
  `ConcurrentAccessError` or other `ArchiveyUsageError` is raised
- **THEN** the usage error propagates past that handler, surfacing the calling-code bug
  instead of being treated as an archive problem

#### Scenario: undeclared concurrent open names the open site

- **WHEN** a second overlapping member stream is opened on a reader without
  `MemberStreams.CONCURRENT`
- **THEN** `ConcurrentAccessError` is raised with the `open_archive()` caller's
  `file:line` in its message, and the first stream is not invalidated

#### Scenario: detected unsupported overlap is a usage error

- **WHEN** an exclusive reader pass/materialization operation is active and a conflicting
  operation begins
- **THEN** the later operation raises `ArchiveyUsageError` and the active operation
  is not invalidated

#### Scenario: post-close reader operation or property is rejected

- **WHEN** any reader method/property other than idempotent `close()` / `__exit__` is used
  after `reader.close()`
- **THEN** `ArchiveyUsageError` is raised
- **AND** an already-open member stream remains governed by the lifecycle-lease contract

#### Scenario: repeated close remains idempotent

- **WHEN** `reader.close()` is called after the reader is already closed
- **THEN** it returns without error or repeated backend teardown

#### Scenario: unsupported positioning uses the standard stream exception

- **WHEN** `seek()` is called on a member stream without declared `SEEKABLE`, or on a
  stream whose backend cannot position
- **THEN** normal `io.UnsupportedOperation` behavior applies, not an archivey-typed error

#### Scenario: teardown error propagates once after state closes

- **WHEN** explicit reader/member close performs final backend teardown and it fails
- **THEN** the translated close error propagates after state becomes irrevocably closed
- **AND** repeated close does not retry or re-raise the teardown

#### Scenario: simultaneous close failures are grouped

- **WHEN** final member close has both an inner-stream close failure and backend teardown
  failure
- **THEN** both translated errors are preserved in an `ExceptionGroup` after state/leases are
  irrevocably released

#### Scenario: caller-owned source closed too early fails as usage error

- **WHEN** a caller closes its supplied `BinaryIO` before an escaped member stream is done
- **THEN** later member I/O raises `ArchiveyUsageError` for the closed source rather
  than returning arbitrary/empty bytes

#### Scenario: declared simultaneous random member streams are not errors

- **WHEN** workers open and operate on independent member streams after materialization
  on a reader declared with `MemberStreams.CONCURRENT`
- **THEN** no concurrency exception is raised
