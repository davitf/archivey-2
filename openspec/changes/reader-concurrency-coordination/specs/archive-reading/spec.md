## ADDED Requirements

These requirements close the two gaps between today's supported `MemberStreams.CONCURRENT`
fan-out seam and a reader that is safe to drive from any thread without the caller
pre-serializing setup and teardown. They coordinate exactly two operations — **first-touch
materialization** and **reader close** — by making them *block and share* instead of
*reject*. Overlapping distinct reader-wide passes and same-stream access are deliberately
left single-owner (final requirement) so "coordinated" does not over-promise.

### Requirement: Coordinated first-touch materialization

A reader opened with `MemberStreams.CONCURRENT` SHALL coordinate concurrent first-touch
operations on a not-yet-materialized member list by blocking all but one caller until the
immutable snapshot is published, rather than rejecting the overlap. Materialization SHALL
run exactly once, and the non-concurrent and uncontended paths SHALL be unchanged.

#### Scenario: concurrent first-touch converges on one materialization

- **WHEN** several threads call `open()`, `members()`, or `get()` simultaneously as the
  first operations on an un-materialized `CONCURRENT` reader
- **THEN** exactly one thread performs materialization while the others block on a
  condition, and once the immutable snapshot is published every waiting thread proceeds
  against it with no thread receiving `ArchiveyUsageError` for the overlap

#### Scenario: failed first-touch wakes every waiter without a partial snapshot

- **WHEN** the electing thread's first-touch materialization fails (for example a corrupt
  header) while other threads are blocked waiting
- **THEN** the cache returns to the un-materialized state, no partial snapshot is ever
  observed, and each waiting thread either observes the same translated error or cleanly
  re-elects a fresh attempt

#### Scenario: uncontended and default paths are unchanged

- **WHEN** materialization happens on a default (non-`CONCURRENT`) reader or with no
  contention
- **THEN** no waiting is introduced, and the member scan, link reads, and callbacks still
  run with no reader-state lock held

### Requirement: Draining reader close

Under `MemberStreams.CONCURRENT`, `reader.close()` SHALL wait for in-flight worker
`open()`/`read()` calls to return and then transition the reader to closed, rather than
raising because workers are active. Escaped open member streams SHALL remain governed by
the existing lifecycle-lease contract, and close idempotency, one-shot teardown, and
post-close rejection SHALL be preserved.

#### Scenario: close drains in-flight worker calls

- **WHEN** a thread calls `reader.close()` while one or more worker `open()`/`read()` calls
  are executing on other threads
- **THEN** `close()` blocks until those calls return, then transitions the reader to
  closed, and does not raise `ArchiveyUsageError` merely because workers were active

#### Scenario: escaped stream survives a drained close

- **WHEN** a member stream that escaped the reader is still open as `close()` returns
- **THEN** it remains readable under the lifecycle-lease contract until its own `close()`,
  and archive teardown runs exactly once after the final lease is released

#### Scenario: concurrent double close is idempotent

- **WHEN** two threads call `reader.close()` (or `__exit__`) simultaneously
- **THEN** teardown runs exactly once, both calls return without error, and simultaneous
  inner-close and teardown failures surface once as an `ExceptionGroup`

#### Scenario: operations after close still reject

- **WHEN** a new `open()` or other reader operation is attempted after `close()` returned
- **THEN** it raises `ArchiveyUsageError` (post-close), unchanged from today

### Requirement: Distinct passes and shared streams remain single-owner

This change SHALL NOT make overlapping *distinct* reader-wide passes or concurrent access
to a single stream object safe; those remain rejected or caller-synchronized exactly as
today, so the coordinated contract stays bounded to materialization and close.

#### Scenario: a different reader-wide pass is still rejected

- **WHEN** a reader is running `extract_all()` or an active `stream_members()` pass and
  another thread starts a different pass (`__iter__`, `stream_members()`, or
  `extract_all()`)
- **THEN** the later operation is rejected with `ArchiveyUsageError`, unchanged

#### Scenario: same-stream access stays the caller's responsibility

- **WHEN** two threads call `read`/`readinto`/`seek`/`close` on the same `ArchiveStream`
  object concurrently
- **THEN** correctness is the caller's responsibility under standard file semantics; this
  change adds no per-stream locking
