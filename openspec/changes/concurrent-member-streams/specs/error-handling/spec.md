# Error Handling — delta (concurrent-member-streams)

## ADDED Requirements

### Requirement: Unsupported concurrency overlap is a usage error

`UnsupportedOperationError` SHALL cover concurrency/state misuse that the reader detects:

- a reader-wide operation overlapping materialization/iteration/streaming/extraction;
- a reader close overlapping an actively executing member-worker call (an idle leased stream
  is not overlap);
- any new reader operation/property except repeated `close()` / `__exit__` after
  `reader.close()`; and
- same-reader password-provider reentry into a password-requiring operation that would
  deadlock.

The error SHALL be raised at the later operation before it changes state and MUST leave
the earlier operation/stream usable. This is API usage in an unsupported state, not a
feature of the archive, so `UnsupportedFeatureError` does not apply.

Internal operation-owner children are not overlap: materialization/worker link reads,
`extract_all()` member/counter peeks and owned `stream_members()` passes, and I/O/close on a
pass's yielded stream carry the root token explicitly. Reentrant public calls do not inherit
that token implicitly and remain rejectable.

The system SHALL NOT define `ConcurrentAccessError`: simultaneous random-access member
streams after materialization are supported and not exceptional.

#### Scenario: detected unsupported overlap is a usage error

- **WHEN** an exclusive reader pass/materialization operation is active and a conflicting
  operation begins
- **THEN** the later operation raises `UnsupportedOperationError` and the active operation
  is not invalidated

#### Scenario: post-close reader operation or property is rejected

- **WHEN** any reader method/property other than idempotent `close()` / `__exit__` is used
  after `reader.close()`
- **THEN** `UnsupportedOperationError` is raised
- **AND** an already-open member stream remains governed by the lifecycle-lease contract

#### Scenario: repeated close remains idempotent

- **WHEN** `reader.close()` is called after the reader is already closed
- **THEN** it returns without error or repeated backend teardown

#### Scenario: unsupported positioning uses the standard stream exception

- **WHEN** `seek()` or `tell()` is unsupported by an otherwise valid member stream
- **THEN** normal `io.UnsupportedOperation` behavior applies, not the reader-state
  `UnsupportedOperationError`

#### Scenario: teardown error propagates once after state closes

- **WHEN** explicit reader/member close performs final backend teardown and it fails
- **THEN** the translated close error propagates after state becomes irrevocably closed
- **AND** repeated close does not retry or re-raise the teardown

#### Scenario: simultaneous close failures are grouped

- **WHEN** final member close has both an inner-stream close failure and backend teardown
  failure
- **THEN** both translated errors are preserved in an `ExceptionGroup` after state/leases are
  irrevocably released

#### Scenario: caller-owned source closed too early fails as state misuse

- **WHEN** a caller closes its supplied `BinaryIO` before an escaped member stream is done
- **THEN** later member I/O raises `UnsupportedOperationError` for the closed source rather
  than returning arbitrary/empty bytes

#### Scenario: simultaneous random member streams are not errors

- **WHEN** workers open and operate on independent member streams after materialization
- **THEN** no concurrency exception is raised
