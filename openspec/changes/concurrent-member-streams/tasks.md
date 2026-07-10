## 1. Replace the public contract

- [ ] 1.1 Apply the `archive-reading` delta: unconditional random-access concurrent member
      streams; materialize-before-fan-out worker seam; unsupported reader-wide overlap;
      `stream_members()` cross-API rules; lifecycle leases; password/callback synchronization
- [ ] 1.2 Apply the `access-mode-and-cost` delta: random-access safety is built in,
      streaming remains exclusive/one-pass, and cost describes expense rather than legality
- [ ] 1.3 Apply the `error-handling` delta: detected overlap and post-close reader use raise
      `UnsupportedOperationError`; do not add `ConcurrentAccessError`
- [ ] 1.4 Apply the `packaging-and-extras` delta, replacing "readers are not thread-safe; one
      per thread" with the narrow supported/unsupported matrix and free-threaded correctness
- [ ] 1.5 Apply the `testing-contract` delta for a concrete `3.13t` core stress job and
      proportionate, non-threshold performance measurements
- [ ] 1.6 Confirm no `allow_multiple_open_streams` parameter, reader flag, documentation,
      or deprecation path is added; this is an API replacement before 1.0

## 2. Materialization and operation state

- [ ] 2.1 Add a lifecycle-independent `UNMATERIALIZED` / `MATERIALIZING` / `MATERIALIZED`
      cache state (never `CLOSED`) and build member/name structures locally before atomic
      publication
- [ ] 2.2 Reject a second operation that overlaps materialization with
      `UnsupportedOperationError`; on build/link/publication failure discard private state,
      return to `UNMATERIALIZED`, and preserve ordinary single-thread lazy retry
- [ ] 2.3 Make the published member/name structures structurally immutable and audit
      list-returning APIs to prevent caller mutation of cache containers; retain existing
      late-bound `ArchiveMember` behavior with idempotent per-member synchronization
- [ ] 2.4 Add unforgeable root operation-owner tokens and explicit private child scopes:
      materialization/worker-open link reads; `extract_all` member/counter peeks and
      `stream_members` passes (including hardlink second pass); and yielded-stream I/O/close;
      never infer ownership from thread identity
- [ ] 2.5 Make public unrelated/reentrant operations tokenless and reject conflicts without
      disturbing the active root/children; release root/child scopes exactly once on normal
      return, exception, generator close, exhaustion, and abandonment/finalization
- [ ] 2.6 Give random `open()` and each random-stream method a short-lived worker token; keep
      only a lifecycle lease while the stream is idle, and let its private lease-bound entry
      capability admit later I/O after reader close without reopening reader APIs
- [ ] 2.7 Update `_open_member` and `ArchiveReader.open` docstrings: concurrent `open` is
      supported after materialization; synchronized bookkeeping is allowed; per-open scratch
      that can be overwritten is forbidden

## 3. Stream lifecycle

- [ ] 3.1 Add lifecycle state `OPEN` / `READER_CLOSED` / `TEARDOWN_RUNNING` /
      `TEARDOWN_COMPLETE`, independent of cache state, with guarded leases and one-shot
      teardown claim
- [ ] 3.2 Reserve a backend-resource lease before eager/lazy `_open_member`; transfer it to
      `ArchiveStream`; release exactly once on never-opened lazy close, initialization failure,
      ordinary close, or finalization
- [ ] 3.3 Refactor `ArchiveStream` lazy initialization from the current stream-lock →
      `open_fn` behavior to `UNOPENED` / `OPENING` / `OPEN` / `FAILED` / `CLOSED`
      claim/call/publish: invoke `open_fn` and inner close with no stream-state lock held
- [ ] 3.4 Make idempotent `reader.close()` mark `READER_CLOSED`, release its lease, and defer
      teardown; reject every later reader operation/property except repeated `close()` /
      `__exit__`, while escaped streams use pre-captured context and remain capability-correct
- [ ] 3.5 Perform teardown outside lifecycle state, mark it complete even on failure, propagate
      a translated failure once from an explicit final closer, never retry, and preserve
      simultaneous inner-close + teardown failures in an `ExceptionGroup`
- [ ] 3.6 Add safety-net finalizers using the same once guards; never raise, run hooks only
      outside locks, report via `sys.unraisablehook` where safe, and preserve rapidgzip/native
      close-before-free guarantees
- [ ] 3.7 Track source ownership: final leases close path handles/Archivey wrappers but never a
      caller-supplied `BinaryIO`; early external close fails later stream use with a typed error
- [ ] 3.8 Ensure reader-close overlap with active worker calls is rejected at the public
      operation boundary; do not promise concurrent-close linearization

## 4. Passwords, callbacks, and diagnostics

- [ ] 4.1 Make static password candidates immutable; protect known-good snapshot/promotion
      and per-unit tried/attempt state without lifecycle/materialization/password locks during
      decrypt/key derivation; permit a required backend/source lock around atomic validation
- [ ] 4.2 Serialize provider resolution with a claim/call/validate/publish condition protocol;
      retain the turn through success/`None`, invoke the provider with no Archivey lock, scope
      validation locks as in 4.1, publish promotion before release, and wake waiters in `finally`
- [ ] 4.3 Detect same-reader password-provider reentry into a password-requiring operation
      and raise `UnsupportedOperationError` rather than self-deadlocking
- [ ] 4.4 Audit selectors, filters, progress callbacks, logging, error stamping/formatting,
      `sys.unraisablehook`, and close/finalizer hooks; no callback may execute under any
      Archivey lock
- [ ] 4.5 Enforce nested reader-state order lifecycle/operation → materialization → password;
      make backend/source locks leaf critical sections and stream state claim/call/publish so
      neither stream → backend nor backend → lifecycle nesting occurs

## 5. Backend compliance

- [ ] 5.1 Audit directory, ZIP, single-file, and SharedSource paths for concurrent
      `open` and independent stream read/readinto/close plus capability-conditional seek/tell
      after materialization; unsupported positioning remains normal `io.UnsupportedOperation`
- [ ] 5.2 Audit native 7z/RAR designs for independent logical position/state, allowing either
      per-open decoders or synchronized bounded/spooled shared decoding; make no guarantee
      against redundant decompression, keep no unsynchronized per-open reader scratch, and
      synchronize password/key caches
- [ ] 5.3 Land `tar-concurrent-open` with one per-reader lock around every shared-handle
      operation, including archive initialization/failure cleanup, TAR `getmembers()` and
      direct EOF reads, member open/context entry, read/readinto, supported seek/tell, member
      close, and archive close
- [ ] 5.4 Record the pinned-library audit: TAR `getmembers()` drives seek/tell/read through
      `_load()`/`next()`; pycdlib `walk()`/`get_record()` are in-memory catalog paths today,
      while `open_file_from_iso` shared caches and `PyCdlibIO.__enter__`/I/O touch shared state;
      add a version-regression probe and lock any path that gains handle access
- [ ] 5.5 Confirm lock wrappers sit below buffering/error wrappers; callbacks/diagnostics stay
      outside, while unavoidable library/source decode may execute in the handle critical section
- [ ] 5.6 Keep streaming-mode `_iter_with_data` single-owner and outside concurrent-open
      support; advancing `stream_members()` closes/invalidates its prior yielded stream

## 6. Authoritative docs and project declarations

- [ ] 6.1 Update `openspec/project.md` target environment and concurrency notes with the
      narrow worker seam; remove the blanket one-reader-per-thread contradiction
- [ ] 6.2 Update `SPEC.md` target environment, `ArchiveReader` lifecycle, access-pattern
      guidance, password provider behavior, and exception hierarchy/overlap semantics
- [ ] 6.3 Update `ARCHITECTURE.md` mutable-member rationale, ABC contract, cache publication,
      operation-owner scopes, lifecycle leases/failure/source ownership, backend compliance
      table, lazy-stream lock protocol, and TAR/ISO mechanism
- [ ] 6.4 Update `PLAN.md` Phase 6 entry gate/tasks so safe concurrent member streams are
      by-construction and not an opt-in or deferred reader retrofit
- [ ] 6.5 Update `IDEAS.md` parallel-extraction entry: the reader worker seam is committed;
      scheduling/throughput remains future and any speed claim needs targeted measurements
- [ ] 6.6 Update `docs/parallel-reader.md` and `docs/threat-model.md` C4 from the historical
      one-reader-per-thread draft to the supported seam/free-threaded correctness stance
- [ ] 6.7 Update public API/ABC docstrings and any user guide to explain materialize-first,
      stream ownership after reader close, positioning capability, same-stream caller
      synchronization, caller-owned sources, operation children, and cost

## 7. Tests, CI, and measurements

- [ ] 7.1 Behavior tests: multiple simultaneous random-access streams read/close and,
      conditionally, seek/tell
      correctly across directory, ZIP path+stream, single-file, plain/compressed TAR, ISO,
      and native 7z/RAR as available
- [ ] 7.2 Worker tests: after `members()`, concurrent `open` by member and name plus
      independent stream operations return exact bytes/positions; non-seekable streams raise
      normal `io.UnsupportedOperation` for unsupported positioning
- [ ] 7.3 State tests: overlapping materialization/pass/extraction/reader-close raises
      `UnsupportedOperationError`; token-bearing materialization/worker link reads, extraction
      member/counter peeks and child passes, yielded-stream I/O, and hardlink recovery succeed;
      reentrant public calls fail
- [ ] 7.4 Lifecycle tests: escaped streams survive `reader.close`, new opens fail, resources
      tear down exactly once after final stream close, failed/lazy opens leak no lease,
      teardown/dual-close failures propagate once with correct chaining/grouping, finalizers
      do not raise, post-close matrix is exact, and caller-owned sources are not closed
- [ ] 7.5 Password tests: concurrent candidates, known-good promotion, one provider callback
      at a time, attempt counts per unit, provider reentry rejection, no callback deadlock
- [ ] 7.6 Lock tests: prove `ArchiveStream.open_fn`/inner close execute without stream-state
      lock, candidate validation may use backend/source locks, and adversarial provider/logging/
      close/finalizer hooks run with no Archivey lock and cannot invert lock order
- [ ] 7.7 `stream_members()` tests: advance invalidates the prior stream; cross-API overlap
      raises without consuming the pass; exhaustion/error/close/abandon closes the current
      stream and releases root/child ownership exactly once
- [ ] 7.8 Add required Linux `free-threaded-concurrency` job to `.github/workflows/ci.yml`:
      `uv python install 3.13t`, `uv sync --python 3.13t --no-dev`, then
      `uv run --python 3.13t --no-sync --with pytest --with pytest-timeout pytest -m
      concurrent_reader`; mark directory, ZIP, single-file stdlib, SharedSource,
      lifecycle/operation state, and TAR tests, and do not count skipped optional backends as
      free-threaded support
- [ ] 7.9 Record a proportionate TAR/ISO lock baseline (wall and wait/hold time; seeks and
      bytes where practical) with no pass/fail threshold; require only targeted before/after
      metrics for later performance claims or strategy changes

## 8. Verification

- [ ] 8.1 `openspec validate --strict concurrent-member-streams`
- [ ] 8.2 `openspec validate --strict tar-concurrent-open`
- [ ] 8.3 `uv run --no-sync ruff check` on touched paths
- [ ] 8.4 `uv run --no-sync pyrefly check` and `uv run --no-sync ty check`
- [ ] 8.5 Run focused and full tests plus the required `3.13t` marked job, then the three
      dependency configurations required by `CONTRIBUTING.md`
