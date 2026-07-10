## Context

The repository currently contains three layers of concurrency history:

1. `shared-source-streams` made archivey-owned byte ranges independently positioned and
   serialized their seek+read pairs.
2. The archived `parallel-reader-exploration` added a reentrant `_open_member` invariant but
   retained a blanket one-reader-per-thread statement and deferred cache synchronization.
3. The replaced opt-in draft added a public
   `allow_multiple_open_streams` gate to make a cost warning impossible to miss.

The third layer solves a performance-discovery problem by weakening a correctness API.
Pre-1.0 there is no reason to preserve that shape. Random-access streams should be independent
objects by construction; callers inspect `cost` to choose an efficient algorithm.

## Goals / Non-Goals

**Goals**

- Correct simultaneous member streams on every random-access backend, with no opt-in.
- A narrow cross-thread contract that is valid on GIL and free-threaded CPython.
- A precise materialization/publication boundary and deterministic resource lifetime.
- Explicit behavior for one-pass APIs and unsupported overlap.
- Synchronization rules for caches, passwords, callbacks, diagnostics, and backend handles.

**Non-Goals**

- Full thread safety for the `ArchiveReader` object.
- Concurrent iteration, materialization, extraction coordination, or reader close.
- Parallel extraction or a speed guarantee.
- Making a single solid block or one TAR/ISO handle execute in parallel.
- Implementing the proposal tasks in this proposal-only change.

## Decisions

### D1. No public opt-in: random-access safety is an invariant

`open_archive()` gains no concurrency keyword. On a random-access reader, simultaneous
member streams are always legal and correct. Any backend that cannot provide independent
logical positions must coordinate its shared handle internally or use independent handles.

`AccessCost.SOLID` and `solid_block_count` remain the signal that independent random opens
may repeat decompression. The library documents and warns about that cost, but does not make
stream correctness conditional. A future higher-level fan-out API may refuse a bad schedule;
the primitive `open()` contract does not.

This replaces, rather than deprecates, the unpublished `allow_multiple_open_streams` draft.
There is no compatibility migration concern before 1.0.

### D2. Supported concurrency is a post-materialization worker seam

The supported cross-thread contract is:

1. One owner completes `members()` / `scan_members()` in random-access mode.
2. The reader atomically publishes the completed internal member tuple and immutable name
   index; list-returning public APIs preserve their signatures by returning a copy that cannot
   mutate those containers.
3. While that snapshot remains published and the reader remains open, any number of workers
   may call `open(member_or_name)` concurrently.
4. Returned streams have independent logical position/state. Different threads may operate
   on different streams via `read`, `readinto`, and `close`, plus `seek`/`tell` when that
   stream supports them. A non-seekable stream retains normal `BinaryIO` behavior
   (`seekable() is False`; unsupported positioning raises `io.UnsupportedOperation`). Each
   individual stream still follows the normal Python file rule: simultaneous operations on
   the **same** stream object require caller synchronization.

The guarantee includes link following and name lookup because both consult the immutable
published snapshot. It does not include `read()` as a separate reader-wide promise, even
though implementations may naturally make it work by composing `open()` with stream I/O.

Reader metadata properties may be read before fan-out when preparing work, but no blanket
concurrent guarantee is made for arbitrary reader methods.

### D3. Materialization is exclusive, lifecycle-independent, and publishes once

The member cache has its own state machine (`UNMATERIALIZED`, `MATERIALIZING`,
`MATERIALIZED`) protected by a short-lived materialization lock. Reader closure is represented
only by the separate lifecycle state in D6; there is no `CLOSED` cache state:

- One thread builds members and the name index in local variables.
- It performs link completion before publication.
- It publishes an internal member tuple and immutable name index atomically under the state
  lock and never mutates those containers afterwards. A public API requiring `list` returns a
  copy rather than exposing a cache container that callers can structurally mutate.
- `ArchiveMember` objects retain their existing backend-populated, late-bound mutability. Any
  such field update reached by random open must be idempotent and synchronized per
  member/backend; conflicting updates are a backend bug, never last-writer-wins behavior.
- Ordinary single-thread `open("name")` may still trigger materialization as today.
  A second operation that overlaps that build is outside the worker seam and raises
  `UnsupportedOperationError` rather than observing a partial cache.
- If building, link completion, or publication fails, local structures are discarded and the
  cache returns to `UNMATERIALIZED`; lifecycle state is unchanged and a later owner may retry.

No callback, decoder, member scan, link-data read, or logging call runs while the
materialization lock is held. The lock protects state transitions/publication only.

### D4. Unsupported reader-wide overlap is explicit

The base reader uses explicit, unforgeable operation-owner tokens rather than thread identity
or a blanket reentrant lock. A public single-owner entry point acquires a root token. Internal
helpers may enter named child scopes only by receiving that token explicitly:

- materialization may perform its own link-data reads while completing the private snapshot;
- a random worker `open()` may perform name lookup, link following, and a synchronized
  late link-data read under its worker token;
- `extract_all()` may inspect available members/source counters and drive one or more
  `stream_members()` child passes, including its random-access hardlink recovery pass;
- a `stream_members()` pass may perform iterator advancement and I/O/close on the stream it
  yielded as child work.

An unrelated public call, even from the same thread or from a callback, has no token and is
not mistaken for a child. It is rejected if it conflicts with the active root operation.
Tokens are capability objects scoped to private call paths; they are never stored in
thread-local state or exposed publicly.

Random `open()` and each random-stream `read`/`readinto`/supported positioning/`close` call
hold a short-lived worker-operation token only for that call; an idle open stream holds a
lifecycle lease, not active operation ownership. The stream carries a private lease-bound
operation-entry capability so its later I/O can still enter after `reader.close()`, whereas
new reader operations cannot. Reader close may therefore proceed when streams are merely
open/idle, but is rejected while a worker call is executing.

Outside those explicit parent/child relationships, `__iter__`, `members`, `scan_members`,
`get_members_if_available` when it would initialize an index view, `stream_members`,
`extract_all`, and reader `close` are single-owner and MUST NOT overlap worker `open()` calls,
member-stream I/O, or one another.

Here "overlap" means concurrent method/I/O execution, not the mere lifetime of an idle open
member stream. D6 deliberately permits a non-concurrent `reader.close()` while member streams
remain open; their leases keep resources alive for later stream I/O.

When the base reader detects overlap through its operation state, it raises
`UnsupportedOperationError` at the later operation without disturbing the earlier one. This
is an API-usage/state error, not an archive feature error. The design adds no
`ConcurrentAccessError`: valid concurrent random access is the capability being guaranteed.

Backends claimed for a free-threaded runtime must still be data-race-free under accidental
overlap (no memory corruption or process crash), but behavior beyond the stated detected
errors is not a supported scheduling contract.

### D5. `stream_members()` is a separate one-pass ownership model

`stream_members()` owns the reader's forward/data pass for its lifetime, in either access
mode. As a public root operation it cannot overlap:

- a second public `stream_members`, `__iter__`, or `extract_all` pass;
- materialization or random `open()` work;
- reader close.

An `extract_all()` root may invoke it with the extraction token as a child, and the yielded
stream's operations are children of the pass rather than conflicting random opens.

The previous yielded stream is closed/invalidated before advancing to the next item. This is
intentional and unlike random `open()`: the iterator owns each stream and advancing transfers
ownership to the next item. A caller that needs simultaneous streams materializes first and
uses random `open()`.

An overlapping one-pass/random operation detected by the base reader raises
`UnsupportedOperationError` before changing pass state. The already-running pass/stream
remains valid. Exhaustion, an exception, explicit generator `close()`, or abandonment/
finalization closes the current yielded stream and releases the pass child scope and root
token exactly once. Finalization is a safety net, not a prompt-release guarantee; callers
should close abandoned generators explicitly.

### D6. Reader close uses stream leases

Lifecycle state is independent of materialization: `OPEN`, `READER_CLOSED`,
`TEARDOWN_RUNNING`, and `TEARDOWN_COMPLETE`, plus a guarded lease count and a one-shot
teardown claim.

Every random-access `open()` reserves a resource lease before eager or lazy backend
initialization and transfers it to the returned `ArchiveStream`. Closing a never-opened lazy
stream, an initialization failure, and ordinary stream close each release the lease exactly
once. A lazy-open failure leaves that stream permanently failed/closed; it cannot be retried
through the same handle. The triggering operation raises the translated open error; later I/O
uses normal closed-stream `ValueError`, and `close()` is a no-op.

`reader.close()` is also idempotent and, when called without concurrent operations:

- atomically marks the reader closed, so all later reader operations except repeated
  `close()` / `__exit__` (including `open`) raise `UnsupportedOperationError`;
- releases the reader's own lease;
- leaves already-open member streams usable;
- performs backend/source teardown exactly once, after the final member-stream lease closes.

Thus a stream may outlive its reader, but resources may outlive the reader context manager as
well. Documentation must say that callers should close member streams promptly; escaping one
from a `with reader` block deliberately extends backend lifetime.

Concurrent reader close with `open()` or stream operations is unsupported rather than a
linearization promise. The operation-state guard rejects it. The last lease claimant marks
`TEARDOWN_RUNNING` under the lifecycle lock, releases that lock, performs backend teardown,
then records `TEARDOWN_COMPLETE`; no path retries teardown. An explicit `reader.close()` or
member `close()` that performs the final teardown propagates a translated teardown error only
after irrevocably closing/releasing its own state. Repeated closes remain no-ops. A safety-net
finalizer uses the same once guards, never raises, and may report a teardown error through
`sys.unraisablehook` only after all Archivey locks are released; native accelerator
`weakref.finalize` guards remain responsible for close-before-free.

Member close releases its lease in a `finally` path even if inner close fails. If inner close
and the resulting final backend teardown both fail, both translated errors are preserved in an
`ExceptionGroup` (Python 3.11+) after all state is irrevocably released. `__exit__` always
performs `close()`; on a clean body a close error propagates normally, and during body
exception unwinding Python's normal exception chaining retains the body exception as the
close error's context.

Path-opened handles and Archivey-created wrappers are reader-owned and live through the final
lease. A caller-supplied `BinaryIO` remains caller-owned: Archivey never closes it, and the
caller must keep it open until the reader and all escaped member streams are finished. If the
caller closes it early, a later stream operation raises `UnsupportedOperationError` for the
closed source; closing it concurrently with I/O is outside the supported contract. Backend
teardown still completes without attempting to close that external object.

The post-close reader matrix is explicit: repeated `close()` and `__exit__` are idempotent;
operations on already-open leased member streams continue according to each stream's
capabilities; every new reader operation/property (`__enter__`, iteration/listing/lookup,
metadata/cost/source counters, `open`/`read`, streaming, and extraction) raises
`UnsupportedOperationError`. Error context already captured by an escaped stream remains
available without calling a closed-reader property. A leased stream's short-lived operation
token remains admissible until that stream closes; it prevents final teardown from racing the
call without reopening the reader API.

### D7. Password resolution is concurrency-safe and callback-safe

The password candidate object is per reader:

- Static sequence candidates are immutable, ordered, and reusable for every encrypted unit.
- The known-good list is snapshot/promoted under a password-state lock, most-recent-success
  first, with duplicate suppression.
- Each encrypted unit has its own tried set and provider attempt counter; attempts for
  different units never overwrite one another.
- Expensive key derivation/decrypt/decode occurs without the lifecycle, operation,
  materialization, or password-state locks. It may acquire the backend/source lock required
  to make a decoder/source operation atomic.
- Success promotion rechecks/deduplicates under the lock.
- Only one provider-driven resolution turn is in flight per reader. A thread claims the turn
  under a condition, releases the condition lock, invokes the provider, and tests returned
  candidates for its encrypted unit without lifecycle, operation, materialization, or
  password-state locks held. Provider invocation itself runs with no Archivey lock held;
  candidate validation may use the narrowly required backend/source lock. The thread retains
  the turn through that provider loop until a candidate succeeds or the provider returns
  `None`; in a `finally` path it publishes the validated outcome, releases the turn, and wakes
  waiters. Therefore callbacks are serialized without running under an internal lock, and a
  waiter re-snapshots the known-good list before claiming the next turn. A password just
  supplied successfully for another unit is promoted before wake-up and avoids a duplicate
  prompt.
- Reentrant provider code that begins another password-requiring operation on the same reader
  is detected and raises `UnsupportedOperationError` rather than waiting on itself. Provider
  calls may perform unrelated application work.

This permits different workers to try immutable candidates concurrently while preventing
duplicate interactive prompts and races in known-good ordering.

### D8. Lock scope, callback rule, and ordering

Nested reader-state acquisition follows lifecycle/operation-state → materialization →
password. Backend/source locks are leaves: code releases reader-state locks before handle or
decoder work except where a narrowly documented atomic transition proves nesting necessary.
Individual stream state uses a claim/call/publish protocol and is never held while acquiring a
backend/source or reader-state lock.

This requires refactoring the actual lazy-open path: `ArchiveStream._ensure_open()` currently
holds `_open_lock` while calling `open_fn`, producing stream → backend acquisition. The new
stream state machine (`UNOPENED`, `OPENING`, `OPEN`, `FAILED`, `CLOSED`) claims `OPENING`
under its condition, releases the condition, invokes `open_fn` (which may use password and
backend/source coordination), then reacquires only to publish success/failure and wake
waiters. Close similarly claims once, releases stream state, closes the inner object under
whatever backend/source lock it requires, then releases the lifecycle lease without nesting
those locks. Teardown claims under lifecycle state and acquires backend/source only after
releasing lifecycle.

Password providers, progress callbacks, selectors/filters, logging handlers, diagnostic
formatting/stamping, `sys.unraisablehook`, and other user-visible close/finalizer hooks execute
with no Archivey lock held. Candidate validation and library/source operations are not
callbacks: they run without lifecycle/materialization/password locks but may hold the required
backend/source lock around atomic handle operations or library-internal decode. Errors are
captured under that lock and translated/stamped/logged after release.

### D9. Backend compliance paths

- **Directory:** independent file descriptor per member.
- **ZIP:** stdlib `_SharedFile` already has independent positions and a `ZipFile` lock; verify
  that member open/close and archive close obey D6.
- **Archivey-owned byte ranges (single-file, native 7z/RAR):** `SharedSource` views with
  per-view positions. Each returned stream has independent logical position/state, but the
  backend may implement that with per-open decoders or synchronized bounded/spooled shared
  decode/materialization. The contract neither requires one decoder per open nor promises
  elimination of redundant decompression.
- **TAR-RA and ISO:** the comprehensive one-lock-per-reader mechanism in
  `tar-concurrent-open`; no sparse/extent reimplementation.

The `_open_member` rule becomes "no unsynchronized open-critical mutation", not a claim of
mathematical purity. Synchronized lifecycle/password/cache accounting is permitted; per-open
scratch that a second call can overwrite is not.

### D10. TAR/ISO lock correctness is intentionally serialized and measured

`tarfile._FileInFile` and stream-backed `pycdlib.PyCdlibIO` perform seek-before-read on one
shared handle. Protecting only a raw `read()` is insufficient: the complete member
`read`/`readinto` call must include its internal seek, while archive/member initialization
and archive close also touch shared state. The wrapper delegates supported `seek`/`tell` and
member close through the same auditable boundary even where the pinned library implements a
particular method using only per-stream state.

Each reader therefore owns one lock covering:

- TAR `tarfile.open()` and ISO `PyCdlib.open()` / `open_fp()` archive initialization and
  failure cleanup;
- TAR `getmembers()` (whose `_load()` performs `next()` seek/tell/read calls) and the direct
  strict-EOF `TarFile.fileobj.read()`;
- TAR `extractfile()` / ISO `open_file_from_iso()` plus ISO stream context entry;
- member `read` and `readinto`;
- member `seek` and `tell` where the inner object supports them;
- member close/context exit;
- archive/library close;
- any other backend call established by audit to reposition or close the same handle.

The pinned pycdlib audit finds `walk()` and `get_record()` traverse the parsed in-memory
directory records and do not access `_cdfp` in the read-only reader; materialization's owner
scope serializes them. They therefore need no handle lock today, but remain explicit audit
items because a supported pycdlib version may change that behavior. `open_file_from_iso()` is
locked regardless: it consults shared library caches and the returned `PyCdlibIO.__enter__`
seeks/tells the image handle.

The lock makes correctness independent of library implementation details between handle
calls, but serializes shared-handle I/O and any library-internal decode performed by those
calls. This is acceptable: the guarantee is safe concurrency, not parallel throughput.
Record a proportionate baseline (wall time and lock wait/hold time, plus seek count/bytes
decompressed where instrumentation is practical) so a later independent-handle, raw-extent,
or native-reader proposal can compare against evidence. The measurement is not a correctness
merge threshold.

### D11. Free-threaded Python is a correctness target

For every claimed backend/runtime combination, the D2 seam must behave identically on regular
and free-threaded CPython. Publication, lease counts, password state, callback gates, and
backend coordination use real synchronization; incidental GIL serialization is not accepted
as correctness.

CI adds a required Linux `free-threaded-concurrency` job to `.github/workflows/ci.yml` using
`uv python install 3.13t`, `uv sync --python 3.13t --no-dev`, and an ephemeral-pytest
`uv run --python 3.13t ... pytest -m concurrent_reader` invocation. The marker covers
directory, ZIP, single-file stdlib codecs, SharedSource, lifecycle, operation scopes, and TAR. Optional
extension-backed cases may remain in ordinary jobs when no `3.13t` wheel exists, but no
backend is claimed free-threaded-safe until it has run in that job or an equivalent dedicated
job. If the CI provider stops supplying `3.13t`, the documented guarantee narrows to the
backends/runtime combinations the required job can execute rather than silently treating a
skip as coverage.

No speedup is promised: shared-source/TAR/ISO locks may serialize I/O, and pure Python/native
codecs have different scaling. Performance work should include measurements proportionate to
the mechanism it changes; benchmark results inform design and substantiate speed claims but
are not a blanket merge gate for correctness fixes.

## Risks / Trade-offs

- **Resource retention:** escaped streams defer backend teardown. This is explicit and safer
  than invalidating live handles or risking native finalizer crashes.
- **Backend serialization:** TAR/ISO and one-handle SharedSource paths are correct but may not
  run in parallel. Benchmarks, not API flags, decide future optimization.
- **Operation-state complexity:** lifecycle, materialization, and operation-owner state are
  separate. Tests cover rollback, child scopes, generator abandonment, and preservation of
  the earlier operation on rejection.
- **Mutable members:** late-bound updates need synchronization or eager completion before
  publication; backend audits must not assume the GIL.
- **Provider reentrancy:** detecting same-reader password reentry is necessary to avoid a
  serialized-provider self-deadlock.

## Migration Plan

This is not a user compatibility migration. During implementation:

1. Replace blanket thread-safety declarations and the old opt-in proposal with the D2 matrix.
2. Add operation/materialization publication and lifecycle leases in the base reader.
3. Synchronize password resolution and member late-bound updates.
4. Audit/adjust each backend; land `tar-concurrent-open` for TAR/ISO.
5. Update authoritative specs and prose docs listed in the tasks.
6. Add behavior/stress tests, the concrete `3.13t` CI job, and proportionate TAR/ISO
   serialization measurements.

## Open Questions

_(none for the contract; backend-specific lock coverage discovered by audit must be added to
`tar-concurrent-open` rather than weakening this guarantee.)_
