## 1. Public contract: declared capabilities, gate, and usage errors

- [x] 1.1 Add the `MemberStreams` flags enum (`CONCURRENT`, `SEEKABLE`) and the
      `member_streams` parameter to `open_archive()`; no `ArchiveyConfig` equivalent, no
      per-`open()` argument
- [x] 1.2 Implement the default single-live-stream gate uniformly on every format
      (directory included): a second overlapping public `open()` raises
      `ConcurrentAccessError`; the first stream stays untouched; the ordinary
      open→read→close loop never triggers it; library-internal opens (extraction,
      hardlink recovery, symlink targets, password confirmation) are exempt
- [x] 1.3 Record the `open_archive()` caller's stack (skip archivey frames for the
      `file:line` breadcrumb) and include it in `ConcurrentAccessError`; retain the full
      captured stack on the reader unconditionally (no config/debug knob)
- [x] 1.4 Add the `ArchiveyUsageError` hierarchy outside `ArchiveyError` with
      `ConcurrentAccessError` as its first subclass; route detected misuse (single-owner
      overlap, post-close reader use, provider reentry, early-closed caller source,
      wrong-reader member identity) to it; keep `UnsupportedOperationError`/
      `UnsupportedFeatureError` as `ArchiveyError`s for archive/mode/feature limitations
- [x] 1.5 Implement default non-seekable member streams (`seekable() is False`,
      `io.UnsupportedOperation` on `seek()`, working `tell()`) for random `open()` and
      `stream_members()` yields on every format; `MemberStreams.SEEKABLE` restores
      backend-provided positioning
- [x] 1.6 Key the `use_rapidgzip`/`use_indexed_bzip2` `AUTO` resolution and native XZ/lzip
      index parsing on declared seek demand (`MemberStreams.SEEKABLE` /
      `open_stream(seekable=True)`); undeclared streams build no index, accelerator, or
      rewind machinery. Public `open_stream(..., seekable=False)` matches the archive-side
      rule.
- [x] 1.7 Reject `streaming=True` combined with `MemberStreams.CONCURRENT` at
      `open_archive()` with `ArchiveyUsageError`; apply the remaining spec deltas;
      confirm neither `allow_multiple_open_streams` nor the unconditional no-flag
      contract survives in any spec or doc

## 2. Materialization and operation state (machinery under CONCURRENT)

- [x] 2.1 Add a lifecycle-independent `UNMATERIALIZED` / `MATERIALIZING` / `MATERIALIZED`
      cache state (never `CLOSED`) and build member/name structures locally before atomic
      publication
- [x] 2.2 Reject a second operation that overlaps materialization with
      `ArchiveyUsageError`; on build/link/publication failure discard private state,
      return to `UNMATERIALIZED`, and preserve ordinary single-thread lazy retry
- [x] 2.3 Make the published member/name structures structurally immutable and audit
      list-returning APIs to prevent caller mutation of cache containers; retain existing
      late-bound `ArchiveMember` behavior with idempotent per-member synchronization
- [x] 2.4 Add unforgeable root operation-owner tokens and explicit private child scopes:
      materialization/worker-open link reads; `extract_all` member/counter peeks and
      `stream_members` passes (including hardlink second pass); and yielded-stream I/O/close;
      never infer ownership from thread identity
- [x] 2.5 Make public unrelated/reentrant operations tokenless and reject conflicts without
      disturbing the active root/children; release root/child scopes exactly once on normal
      return, exception, generator close, exhaustion, and abandonment/finalization
- [x] 2.6 Give random `open()` and each random-stream method a short-lived worker token; keep
      only a lifecycle lease while the stream is idle. A stream's own I/O
      (`read`/`readinto`/`seek`/`tell`/`close`) is routed around the operation-owner gate
      entirely (touching only its lease + backend), so later I/O after reader close is
      admissible with **no** separate lease-bound entry capability object (D7 simplification)
- [x] 2.7 Update `_open_member` and `ArchiveReader.open` docstrings: concurrent `open` is
      supported after materialization under `MemberStreams.CONCURRENT`; synchronized
      bookkeeping is allowed; per-open scratch that can be overwritten is forbidden
- [x] 2.8 Ensure the undeclared default path takes no shared-handle locks and no lease
      accounting beyond its single stream; the machinery activates with the declared
      capability

## 3. Stream lifecycle

- [x] 3.1 Add lifecycle state `OPEN` / `READER_CLOSED` / `TEARDOWN_RUNNING` /
      `TEARDOWN_COMPLETE`, independent of cache state, with guarded leases and one-shot
      teardown claim (leases apply to default readers too — one escaped stream can
      outlive its reader)
- [x] 3.2 Reserve a backend-resource lease before eager/lazy `_open_member`; transfer it to
      `ArchiveStream`; release exactly once on never-opened lazy close, initialization failure,
      ordinary close, or finalization
- [x] 3.3 Refactor `ArchiveStream` lazy initialization so `open_fn` and the inner close run
      with **no stream-state lock held** (the essential rule: never nest stream → backend
      under teardown's backend acquisition, D13). A minimal claimed-to-open flag guarding a
      single caller into `open_fn` outside the lock is sufficient; the `UNOPENED` / `OPENING`
      / `OPEN` / `FAILED` / `CLOSED` enum is optional documentation, not a requirement
- [x] 3.4 Make idempotent `reader.close()` mark `READER_CLOSED`, release its lease, and defer
      teardown; reject every later reader operation/property except repeated `close()` /
      `__exit__` with `ArchiveyUsageError`, while escaped streams use pre-captured context
      and remain capability-correct
- [x] 3.5 Perform teardown outside lifecycle state, mark it complete even on failure, propagate
      a translated failure once from an explicit final closer, never retry, and preserve
      simultaneous inner-close + teardown failures in an `ExceptionGroup`
- [x] 3.6 Add safety-net finalizers using the same once guards; never raise, run hooks only
      outside locks, report via `sys.unraisablehook` where safe, and preserve rapidgzip/native
      close-before-free guarantees
- [x] 3.7 Track source ownership: final leases close path handles/Archivey wrappers but never a
      caller-supplied `BinaryIO`; early external close fails later stream use with
      `ArchiveyUsageError`
- [x] 3.8 Ensure reader-close overlap with active worker calls is rejected at the public
      operation boundary; do not promise concurrent-close linearization

## 4. Passwords, callbacks, and diagnostics

- [x] 4.1 Make static password candidates immutable; protect known-good snapshot/promotion
      and per-unit tried/attempt state without lifecycle/materialization/password locks during
      decrypt/key derivation; permit a required backend/source lock around atomic validation
- [x] 4.2 Serialize provider resolution with a **simple lock released around the callback**
      (invoke the provider with no Archivey lock, scope validation locks as in 4.1, publish
      promotion under the lock). Do **not** build the resolution-turn condition protocol
      (D10 simplification): concurrent first-touch may call the provider / attempt a
      candidate redundantly, which is acceptable because promotion stays synchronized and
      convergent
- [x] 4.3 Detect same-reader password-provider reentry into a password-requiring operation
      and raise `ArchiveyUsageError` rather than self-deadlocking
- [x] 4.4 Audit selectors, filters, progress callbacks, logging, error stamping/formatting,
      `sys.unraisablehook`, and close/finalizer hooks; no callback may execute under any
      Archivey lock
- [x] 4.5 Enforce nested reader-state order lifecycle/operation → materialization → password;
      make backend/source locks leaf critical sections and stream state claim/call/publish so
      neither stream → backend nor backend → lifecycle nesting occurs

## 5. Backend compliance

- [x] 5.1 Audit directory, ZIP, single-file, and SharedSource paths for declared concurrent
      `open` and independent stream read/readinto/close plus capability-conditional seek/tell
      after materialization; unsupported positioning remains normal `io.UnsupportedOperation`
- [x] 5.2 Audit native 7z/RAR designs for independent logical position/state, allowing either
      per-open decoders or synchronized bounded/spooled shared decoding; make no guarantee
      against redundant decompression, keep no unsynchronized per-open reader scratch, and
      synchronize password/key caches.
      **REMINDER:** readers are not implemented yet — land a design-note audit in this change;
      code compliance waits on the native 7z/RAR reader phases.
- [x] 5.3 Land `tar-concurrent-open` with one per-reader lock around every shared-handle
      operation, instantiated only for `CONCURRENT` readers, including archive
      initialization/failure cleanup, TAR `getmembers()` and direct EOF reads, member
      open/context entry, read/readinto, supported seek/tell, member close, and archive close
- [x] 5.4 Record the pinned-library audit: TAR `getmembers()` drives seek/tell/read through
      `_load()`/`next()`; pycdlib `walk()`/`get_record()` are in-memory catalog paths today,
      while `open_file_from_iso` shared caches and `PyCdlibIO.__enter__`/I/O touch shared state;
      add a version-regression probe and lock any path that gains handle access
- [x] 5.5 Confirm lock wrappers sit below buffering/error wrappers; callbacks/diagnostics stay
      outside, while unavoidable library/source decode may execute in the handle critical section
- [x] 5.6 Keep streaming-mode `_iter_with_data` single-owner and outside concurrent-open
      support; advancing `stream_members()` closes/invalidates its prior yielded stream

## 6. Authoritative docs and project declarations

- [x] 6.1 Update `openspec/project.md` target environment and concurrency notes with the
      declared-capabilities matrix; remove the blanket one-reader-per-thread contradiction
- [x] 6.2 Update `SPEC.md` target environment, `ArchiveReader` lifecycle, access-pattern
      guidance, password provider behavior, and the two exception hierarchies
      (`ArchiveyError` vs `ArchiveyUsageError`)
- [x] 6.3 Update `ARCHITECTURE.md` mutable-member rationale, ABC contract, cache publication,
      operation-owner scopes, lifecycle leases/failure/source ownership, backend compliance
      table, lazy-stream lock protocol, TAR/ISO mechanism, and the capability gate
- [x] 6.4 Update `PLAN.md` Phase 6 entry gate/tasks: concurrent member streams are a declared
      capability with correct-by-construction machinery underneath
- [x] 6.5 Update `IDEAS.md` parallel-extraction entry: the declared worker seam is committed;
      scheduling/throughput remains future and any speed claim needs targeted measurements
- [x] 6.6 Update `docs/parallel-reader.md` and `docs/threat-model.md` C4 to the
      declared-capabilities stance
- [x] 6.7 Update public API/ABC docstrings and the user guide: the `member_streams`
      declaration (with the explicit note that solid open-order cost is NOT covered by the
      gate — see `AccessCost`/`stream_members()`), materialize-first, stream ownership after
      reader close, positioning capability, same-stream caller synchronization, caller-owned
      sources, and the usage-error hierarchy
- [x] 6.8 Record the directory-uniformity principle (`format-directory` delta) in the
      directory reader's module docstring

## 7. Tests, CI, and measurements

- [x] 7.1 Gate tests: the capability matrix of the `testing-contract` delta — uniform
      `ConcurrentAccessError` (with open-site breadcrumb) across every format including
      directory; sequential loop unaffected; default non-seekability everywhere;
      `SEEKABLE` restores positioning; extraction ungated; usage errors escape
      `except ArchiveyError`; demand-driven accelerator activation
- [x] 7.2 Worker tests (declared `CONCURRENT`): after `members()`, concurrent `open` by member
      and name plus independent stream operations return exact bytes/positions; non-seekable
      streams raise normal `io.UnsupportedOperation` for unsupported positioning
- [x] 7.3 State tests (**cooperative / v1**): overlapping materialization/pass/extraction/
      reader-close raises `ArchiveyUsageError`; token-bearing materialization/worker link
      reads, extraction member/counter peeks and child passes, yielded-stream I/O, and
      hardlink recovery succeed; same-thread reentrant public calls fail.
      **REMINDER (post-v1 / D15):** multi-thread adversarial overlap & free-threaded stress
      of this matrix lands with the `CONCURRENT` promotion, not in the provisional ship.
- [x] 7.4 Lifecycle tests (**cooperative / v1**): escaped streams survive `reader.close`,
      new opens fail as usage errors, resources tear down exactly once after final stream
      close, failed/lazy opens leak no lease, teardown/dual-close failures propagate once
      with correct chaining/grouping, finalizers do not raise, post-close matrix is exact,
      and caller-owned sources are not closed.
      **REMINDER (post-v1 / D15):** free-threaded teardown races and adversarial
      finalizer/close interleaving land with the promotion.
- [x] 7.5 Password tests: concurrent candidates, known-good promotion converges, provider
      invoked under no Archivey lock, provider reentry rejection, no callback deadlock (per
      D10 the provider is **not** guaranteed to be called at most once under concurrent
      first-touch; assert convergence and no corruption, not single-call)
- [x] 7.6 Lock tests (**cooperative / v1**): prove `ArchiveStream.open_fn`/inner close
      execute without stream-state lock; provider/logging/close hooks are not invoked under
      an Archivey lock on the cooperative path.
      **REMINDER (post-v1 / D15):** adversarial lock-order inversion probes and free-threaded
      lock stress land with the promotion.
- [x] 7.7 `stream_members()` tests: advance invalidates the prior stream; cross-API overlap
      raises without consuming the pass; exhaustion/error/close/abandon closes the current
      stream and releases root/child ownership exactly once
- [ ] 7.8 **DEFERRED (post-v1, D15) — do not implement while `CONCURRENT` is provisional.**
      Add required Linux `free-threaded-concurrency` job to `.github/workflows/ci.yml`:
      `uv python install 3.13t`, `uv sync --python 3.13t --no-dev`, then
      `uv run --python 3.13t --no-sync --with pytest --with pytest-timeout pytest -m
      concurrent_reader`; mark directory, ZIP, single-file stdlib, SharedSource,
      lifecycle/operation state, and TAR tests, and do not count skipped optional backends as
      free-threaded support. Lands when `CONCURRENT` is promoted from provisional to supported.
- [ ] 7.9 **DEFERRED (post-v1 with TAR/ISO measurement pass) — reminder only.** Record a
      proportionate TAR/ISO lock baseline (wall and wait/hold time; seeks and bytes where
      practical) with no pass/fail threshold; require only targeted before/after metrics for
      later performance claims or strategy changes. Companion: `tar-concurrent-open` §5.

## 8. Verification

- [x] 8.1 `openspec validate --strict concurrent-member-streams`
- [x] 8.2 `openspec validate --strict tar-concurrent-open`
- [x] 8.3 `uv run --no-sync ruff check` on touched paths
- [x] 8.4 `uv run --no-sync pyrefly check` and `uv run --no-sync ty check`
- [x] 8.5 Run focused and full tests, then the three dependency configurations required by
      `CONTRIBUTING.md`.
      **REMINDER:** the required `3.13t` marked job is part of deferred task 7.8 — skip it
      while `CONCURRENT` is provisional.

## Sequencing note

The API shape (the `member_streams` parameter, both bits, the usage-error hierarchy)
lands whole in the first implementation step together with the `CONCURRENT` gate and its
machinery (sections 1–5). The `SEEKABLE` machinery flip (task 1.5/1.6 internals —
demand-driven accelerators, non-seekable defaults through `seekable-decompressor-streams`
and the single-stream API) may land as a second step; until it does, declared `SEEKABLE`
preserves today's behavior.

**Provisional `CONCURRENT` (D15).** v1 lands the load-bearing correctness machinery
(tokens/child scopes, materialization publication, leases, the gate, the TAR/ISO handle
lock) and the *cooperative-use* guarantee. The adversarial/free-threaded hardening —
task 7.8's required `3.13t` CI job and the heavier interleaving/lock stress in
7.3/7.4/7.6 — is deferred until `CONCURRENT` is promoted from provisional to supported.
Tasks 6.x must state the provisional status in the docstrings and capability matrix.
