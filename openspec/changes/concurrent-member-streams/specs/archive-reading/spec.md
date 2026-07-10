# Archive Reading — delta (concurrent-member-streams)

## MODIFIED Requirements

### Requirement: Multiple concurrently-open member streams

Every **random-access** reader (`streaming=False`) SHALL support any number of member
streams opened from that single reader being held open simultaneously and read in
interleaved order without corrupting one another. This is an unconditional correctness
property; `open_archive()` has no concurrent-stream opt-in and access cost does not determine
legality.

**Post-materialization worker seam.** After one owner has completed `members()` or
`scan_members()` and the reader has published its member list/name index, concurrent calls
from multiple threads to `open(member_or_name)` SHALL be supported. Streams returned by
different opens SHALL have independent logical positions/state: workers MAY concurrently call
`read`, `readinto`, and `close` on **different stream objects**, plus `seek`/`tell` where that
stream supports positioning. Non-seekable streams retain normal `BinaryIO` behavior:
`seekable()` is false and unsupported positioning raises `io.UnsupportedOperation`.
Simultaneous operations on the same stream object require caller synchronization, matching
ordinary Python file objects. The supported behavior SHALL NOT rely on the GIL.

**Materialization boundary.** The members and name index SHALL be built in private/local
state, completed (including link resolution), and published together exactly once as immutable
internal containers. A public API whose existing return type is `list` SHALL return a
copy that cannot structurally mutate those cache containers. `ArchiveMember` objects
retain their existing backend-populated late-bound fields. Materialization/iteration itself
is a single-owner operation and MUST NOT overlap worker opens. A second operation detected
while materialization is in progress SHALL raise `UnsupportedOperationError` without
observing a partial cache or disturbing the first operation. Late-bound random-open updates
to a member MUST be idempotent and synchronized; conflicting last-writer-wins updates are
forbidden. Materialization state is exactly `UNMATERIALIZED` / `MATERIALIZING` /
`MATERIALIZED`; reader lifecycle is separate and MUST NOT add `CLOSED` to the cache state.
A failed build discards private state and returns to `UNMATERIALIZED`.

**Backend compliance.** Archivey-owned byte-range backends MUST use views with per-view
positions and atomic shared-source handle operations. External-library backends MUST provide
equivalent coordination: ZIP MAY rely on stdlib `_SharedFile`; random-access TAR and ISO MUST
use the one-per-reader lock specified by `tar-concurrent-open`, covering every operation on
the shared library handle. A solid format satisfies correctness by giving each returned
stream independent logical position/state. It MAY use per-open decoders or a synchronized,
bounded/spooled shared decode/materialization strategy; the contract neither requires
one decoder per open nor promises elimination of redundant decompression.

**Reader-wide operation ownership.** Public iteration, materialization, `scan_members`,
`get_members_if_available` initialization, `stream_members`, `extract_all`, and reader
`close` are single-owner operations and cannot overlap one another or the random worker seam.
The base reader SHALL represent ownership with an explicit unforgeable root token, not thread
identity. Private helpers MAY receive that token to enter child scopes: materialization may
perform link-data reads; a random worker `open()` may do name lookup/link following and late
link-data reads; `extract_all` may inspect available members/source counters and drive one or
more `stream_members` passes; and a pass may advance and perform I/O/close on its yielded
stream. An unrelated/reentrant public call has no token even on the owner thread and is
rejected. The later conflicting operation SHALL raise `UnsupportedOperationError` before
changing state; the earlier root and children remain usable.

Random `open()` and each operation on a random-open stream SHALL hold a short-lived worker
token only while that call executes. An idle open stream owns a lifecycle lease, not active
operation ownership. It carries a private lease-bound entry capability so later stream I/O
remains admissible after `reader.close()`. Thus reader close MAY run while streams are idle,
but is rejected while a worker call is executing; closure does not enable any new reader API.

"Overlap" means concurrent method/I/O execution, not the lifetime of an idle open member
stream: a non-concurrent `reader.close()` MAY run while member streams remain open, and their
leases preserve resources for later stream I/O. This is not a blanket thread-safety guarantee
for every reader method.

**`stream_members()` is separate.** A `stream_members()` pass owns the reader's one-pass
data path. It MUST NOT overlap random `open()` work or any other forward/data pass.
Advancing the iterator closes/invalidates the previously yielded stream before yielding the
next; this iterator-owned lifecycle does not apply to independent streams returned by random
`open()`. The yielded stream carries a child scope so its I/O is permitted during the pass.
Exhaustion, exceptions, explicit generator close, and generator abandonment/finalization
SHALL close the current yielded stream and release the pass scope/token exactly once. A caller
needing simultaneous streams SHALL materialize and use random `open()`.

**Cost is informational.** `AccessCost.SOLID` / `solid_block_count` tell callers that
simultaneous random streams may repeat decompression. They never disable the guarantee.
`stream_members()` remains the efficient bounded-memory, one-decode path for a sequential
solid-archive workload.

**Detectable closed-source misuse and bounds.** A live lease prevents reader-owned backend
resources from closing underneath a member stream. If a caller-owned source is nevertheless
closed externally, the reader surface SHALL raise a typed error rather than return arbitrary
or empty bytes. Archivey shared-source views clamp bounds extending past the available source
like normal streams, so truncation produces a short view rather than a construction failure.

**Callbacks and lock scope.** Password providers, selectors/filters, progress callbacks,
logging handlers, diagnostic formatting/stamping, `sys.unraisablehook`, and user-visible
close/finalizer hooks MUST execute without any Archivey lock held. Decode/password candidate
validation is not a callback: it MUST run without lifecycle/operation, materialization, or
password locks, but MAY hold the narrowly required backend/source lock around an atomic
decoder/handle operation. Nested reader-state order is lifecycle/operation → materialization
→ password. Backend/source locks are leaves. Individual stream state uses claim/call/publish
and MUST be released before invoking lazy `open_fn`, inner I/O/close, backend/source
operations, or lifecycle lease release.

#### Scenario: workers open and operate on independent streams after materialization

- **WHEN** a random-access reader has completed `members()` and two threads concurrently
  call `open()` for different members, then use the operations each stream supports
- **THEN** each stream returns exactly its member's bytes and keeps its own position, with no
  cache, reader-state, or source-position race

#### Scenario: simultaneous streams need no opt-in

- **WHEN** a caller opens two random-access member streams and reads them interleaved
- **THEN** both are correct without any `allow_multiple_open_streams` argument or cost-based
  gate

#### Scenario: materialization overlap is rejected without partial publication

- **WHEN** one thread is materializing a reader and another operation attempts to
  materialize or open from the not-yet-published cache
- **THEN** the later operation raises `UnsupportedOperationError`, and no caller observes a
  partial member list/name index

#### Scenario: materialization failure does not close or poison the cache

- **WHEN** a materialization owner fails before publication
- **THEN** its private structures are discarded, cache state returns to `UNMATERIALIZED`,
  and lifecycle remains independently `OPEN`

#### Scenario: reader-wide mutation does not overlap the worker seam

- **WHEN** worker member-stream operations are active and iteration, `stream_members`,
  `extract_all`, or reader `close()` is attempted concurrently
- **THEN** the detected later operation raises `UnsupportedOperationError` without closing
  or corrupting the active member streams

#### Scenario: solid concurrent streams are correct but may repeat work

- **WHEN** two members in one solid block are opened simultaneously
- **THEN** each stream returns correct independent bytes; `AccessCost.SOLID` describes the
  possible re-decode cost, and no concurrency exception is raised

#### Scenario: free-threaded correctness does not depend on the GIL

- **WHEN** the post-materialization worker scenario runs for a backend/runtime combination
  covered by the required CPython `3.13t` job
- **THEN** cache publication, lifecycle leases, password state, and member/source positions
  remain data-race-free with the same observable results as a regular build

#### Scenario: unsupported seek keeps normal stream semantics

- **WHEN** a returned member stream reports `seekable() is False` and the caller seeks
- **THEN** it raises `io.UnsupportedOperation` rather than gaining a synthetic seek guarantee

#### Scenario: extraction is an owner with permitted child passes

- **WHEN** `extract_all()` drives `stream_members()`, reads/closes its yielded streams, and
  performs a random-access hardlink recovery pass
- **THEN** those token-bearing child scopes are permitted while an unrelated public operation
  is rejected

#### Scenario: externally closed source fails loudly

- **WHEN** a caller-owned underlying source is externally closed while a member stream still
  holds a reader lease
- **THEN** the reader surface raises a typed error rather than returning arbitrary or empty
  bytes

### Requirement: Random-access member-open is reentrant and reader-state-free

For every random-access backend, `_open_member` SHALL derive the returned stream from the
member plus immutable/published archive state and coordinated backend resources. It MUST
NOT keep unsynchronized per-open scratch on the reader that another open can overwrite.
Synchronized shared bookkeeping—operation state, stream leases, password/key caches, and
backend handle locks—is permitted and required where applicable.

Archivey-owned byte ranges MUST use shared-source views with per-view position. A
library-owned seek-before-read backend (random-access TAR/ISO) MUST coordinate the complete
shared-handle lifecycle through its per-reader lock. Immutable member/name structures MAY
be read concurrently after materialization.

Forward-only/streaming passes remain out of scope because they own one progressive decoder
and cannot overlap. There is no random-access TAR/ISO exemption: those backends satisfy the
random-access invariant through their locked library streams.

#### Scenario: one open cannot overwrite another open's state

- **WHEN** two post-materialization `open()` calls execute concurrently
- **THEN** neither stores unsynchronized per-open state on the reader, and both returned
  streams remain correct under interleaving

#### Scenario: TAR and ISO comply through comprehensive handle locking

- **WHEN** a random-access TAR or ISO reader opens/uses multiple member streams
- **THEN** every required shared-handle/library decode operation is serialized by its one
  per-reader lock, while archivey callbacks and diagnostics run with no Archivey lock

### Requirement: Bounded-memory sequential streaming via stream_members

The system SHALL provide `stream_members()` which yields `(member, stream)` pairs in archive
order with bounded memory. A solid block is decompressed progressively and never buffered
whole in memory; peak memory is the decoder working set plus one in-flight chunk. Non-file
members yield `None`.

`members` is a selector (a collection of names/member identities or a predicate), not a
transform. Streams are lazy: unselected or unread members are not opened/decompressed and
do not request passwords. The generator yields the original mutable `ArchiveMember` so
late-bound fields remain visible; transformation stays at extraction/writing sinks.

The yielded stream is owned by the iterator and valid only until advance: before obtaining
the next item, the iterator SHALL close/invalidate the previous stream. The implementation
MUST NOT retain a growing decompressed-block cache until reader close. On a solid archive,
random `open()` may re-decode from the block start and warn callers to prefer
`stream_members()` for a sequential pass.

A `stream_members()` invocation is an exclusive one-pass/data-path operation in both access
modes. It SHALL NOT overlap random `open()`, materialization, another iteration/data pass,
an unrelated extraction, or reader close. An `extract_all()` owner MAY invoke it as a child
pass and MAY read/close the yielded child stream. Detected unrelated overlap SHALL raise
`UnsupportedOperationError` at the later operation and leave the active pass/stream valid.
This differs deliberately from random `open()`, whose independently owned streams may
coexist.

#### Scenario: advance releases the previous iterator-owned stream

- **WHEN** a caller advances `stream_members()` after receiving one member stream
- **THEN** the prior stream is closed/invalidated before the next pair is yielded

#### Scenario: random open cannot overlap a streaming pass

- **WHEN** a `stream_members()` pass is active and a random `open()` is attempted
- **THEN** `UnsupportedOperationError` is raised and the active pass remains usable

#### Scenario: abandoned streaming generator releases ownership

- **WHEN** a caller explicitly closes or abandons a partially consumed `stream_members()`
  generator
- **THEN** its current yielded stream is closed and its child/root operation scopes are
  released exactly once

### Requirement: Context-manager and close lifecycle

The reader SHALL implement `__enter__`, `__exit__`, and explicit `close()`. Lifecycle state
(`OPEN`, `READER_CLOSED`, `TEARDOWN_RUNNING`, `TEARDOWN_COMPLETE`) and lease count SHALL be
guarded independently from materialization. `ArchiveReader.close()` SHALL be idempotent.
Called without unsupported concurrent operations, it atomically marks `READER_CLOSED`.

Each random-open member stream SHALL own a backend-resource lease. Already-open member
streams remain usable according to their individual capabilities after reader close and keep
the required backend resources alive. Backend/source teardown SHALL occur exactly once after
both the reader is closed and the final member-stream lease is released. A failed open releases its reserved
lease; this includes lazy initialization failure and closing a lazy stream before first use.
The final releaser claims teardown under the lifecycle lock, performs it after releasing that
lock, and records completion without retry. Backend teardown and inner stream close execute
outside lifecycle locks. A lazy-open failure raises its translated error from the triggering
operation, permanently releases/closes that handle, makes later I/O raise normal closed-stream
`ValueError`, and leaves repeated stream `close()` a no-op.

If explicit reader/member close triggers final teardown and teardown fails, the closer SHALL
be irrevocably closed and the translated error SHALL propagate once; repeated closes SHALL
not retry or re-raise it. A safety-net finalizer SHALL use the same once guards, never raise,
and MAY report through `sys.unraisablehook` only outside all Archivey locks. Native
accelerator finalizers retain their close-before-free guarantee.

Member close SHALL release its lease in `finally` even when inner close fails. If inner close
and the resulting final backend teardown both fail, both translated errors SHALL be preserved
in an `ExceptionGroup`. `__exit__` SHALL always call `close()`; a close failure propagates on
normal exit, and during body-exception unwinding the body exception remains available through
normal Python exception chaining.

Archivey SHALL close path handles and wrappers it owns only after the final lease. It SHALL
never close a caller-supplied `BinaryIO`; the caller must keep it open through all reader and
escaped-stream use. If the caller closes it early, a later operation raises a typed
`UnsupportedOperationError` for the closed source; concurrent external close with I/O is
unsupported.

Consequently, exiting `with open_archive(...) as reader` closes the reader but an escaped
member stream intentionally extends backend resource lifetime until that stream closes.
Callers SHOULD close member streams promptly. Concurrent reader close with `open()` or
member-stream operations is unsupported and is rejected; no close-vs-I/O linearization is
promised.

After reader close, repeated `close()` / `__exit__` are no-ops and already-open streams
continue according to their capabilities. Every new reader operation or property—including
`__enter__`, iteration/listing/lookup, metadata/cost/source counters, `open`/`read`,
`stream_members`, and extraction—SHALL raise `UnsupportedOperationError`. Escaped streams
use context captured before close for error translation and MUST NOT call those properties.
Their lease-bound short-lived worker tokens prevent final teardown from racing each call.

#### Scenario: escaped member stream survives reader close

- **WHEN** a member stream is opened, then the reader is closed without concurrent I/O
- **THEN** new reader operations raise `UnsupportedOperationError`, while the existing
  stream remains usable until it is closed
- **AND** backend teardown occurs exactly once after that final stream close

#### Scenario: idle lease is not active overlap

- **WHEN** a random-open stream is idle and `reader.close()` runs
- **THEN** close succeeds and releases the reader lease
- **AND** later operations on that stream use its lease-bound worker entry until stream close

#### Scenario: failed eager member open leaks no lifecycle lease

- **WHEN** `_open_member` raises after reserving a resource lease
- **THEN** the reservation is released and a later reader close can complete teardown

#### Scenario: failed lazy member open closes its handle

- **WHEN** first I/O on a lazy member handle makes `_open_member` raise
- **THEN** the translated error is surfaced, its lease is released, later I/O gets normal
  closed-stream `ValueError`, and repeated close is a no-op

#### Scenario: final teardown failure is attempted once

- **WHEN** explicit reader or final-stream close claims teardown and backend close raises
- **THEN** that closer is still irrevocably closed, the translated error propagates once,
  lifecycle reaches `TEARDOWN_COMPLETE`, and repeated close does not retry

#### Scenario: member and teardown close failures are both preserved

- **WHEN** final member close encounters both an inner-close error and backend teardown error
- **THEN** its lease/state are still released exactly once and an `ExceptionGroup` preserves
  both translated failures

#### Scenario: caller-owned source is never closed by Archivey

- **WHEN** a reader and all escaped streams over a caller-supplied `BinaryIO` are closed
- **THEN** Archivey releases its wrappers but does not call `close()` on that source

#### Scenario: context exit closes the reader

- **WHEN** an `open_archive()` context exits normally or through an exception
- **THEN** the reader is marked closed and its lease is released
- **AND** backend resources are released immediately unless an escaped member stream still
  owns a lease

### Requirement: Password candidates and provider

`password` SHALL accept a single `str | bytes`, an ordered sequence, or a provider
`Callable[[PasswordRequest], str | bytes | None]`. `PasswordRequest.member` identifies the
encrypted member (or is `None` for archive-level/header decryption), and `attempt` starts at
one and increments after a provider result fails for that unit.

For each encrypted unit, resolution SHALL try the per-reader known-good list (most recent
first), then the ordered static candidates not yet tried for that unit, then call the
provider repeatedly until success or `None`. Every success is promoted to known-good so
later units can reuse it. Exhaustion raises `EncryptionError`; `open()`/`read()` have no
per-call password parameter.

This existing order SHALL be safe for concurrent post-materialization opens. Static
candidates are immutable and ordered. Known-good snapshots/promotions and per-unit
tried/attempt state are synchronized; expensive key derivation/decryption is performed
without lifecycle/operation, materialization, or password-state locks, but MAY use a required
backend/source lock around an atomic decode/handle operation.

At most one provider-driven resolution turn may be active per reader. Provider invocation
SHALL use a claim/call/validate/publish protocol: claim the turn under a condition, release
all Archivey locks, call the provider, then test returned candidates for that encrypted unit
without lifecycle/materialization/password locks (using a required backend/source lock only
for atomic validation work). It then publishes the validated outcome, releases the turn, and
wakes waiters in a `finally` path. The turn remains claimed through repeated provider attempts
until success or `None`. A waiter SHALL recheck known-good passwords before claiming the next
turn, avoiding a duplicate prompt because a successful result is promoted before wake-up.
Provider callbacks are therefore serialized and always lock-free. Reentrant provider code
that starts another password-requiring operation on the same reader SHALL raise
`UnsupportedOperationError` rather than deadlocking. Attempt counts remain per encrypted
unit.

#### Scenario: concurrent encrypted opens share known-good state safely

- **WHEN** workers concurrently open differently encrypted units after materialization
- **THEN** each follows candidate order independently, successful passwords are promoted
  without races/duplicates, and no unit overwrites another's attempt state

#### Scenario: provider exhaustion remains an encryption failure

- **WHEN** known-good and static candidates fail and the provider returns `None`
- **THEN** `EncryptionError` is raised for that encrypted unit

#### Scenario: provider callbacks are serialized without internal locks

- **WHEN** two workers need the password provider at once
- **THEN** only one provider callback runs at a time with no Archivey lock held; validation
  holds no lifecycle/materialization/password lock but may use its required backend/source
  lock, and the waiter resumes after the validated outcome is published

#### Scenario: password-provider same-reader reentry fails instead of deadlocking

- **WHEN** a provider callback starts another password-requiring operation on the same reader
- **THEN** that nested operation raises `UnsupportedOperationError`
