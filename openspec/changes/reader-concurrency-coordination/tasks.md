## 1. Blocking first-touch materialization

- [x] 1.1 In `ReaderState`, replace the "second overlapping materialization →
      `ArchiveyUsageError`" path with a condition-variable wait: `MATERIALIZING` blocks
      later first-touch callers; on `complete_materialization` the holder notifies all
      waiters, which then read the published snapshot; the overlap no longer raises.
- [x] 1.2 On `fail_materialization`, return to `UNMATERIALIZED`, wake all waiters, and let
      them re-elect a fresh attempt or observe the same translated error — never publish a
      partial snapshot.
- [x] 1.3 Keep the heavy work (member scan, link reads, callbacks) outside the reader-state
      lock; only the short wait/notify is under it. Materialization still runs exactly once.
- [x] 1.4 Leave the uncontended and default (non-`CONCURRENT`) paths byte-for-byte
      unchanged — no wait when there is no contention.

## 2. Draining reader close

- [x] 2.1 In `ReaderState.mark_reader_closed`, when workers are active under `CONCURRENT`,
      wait on a condition until the active worker-token set drains, then mark
      `READER_CLOSED` — instead of raising because workers are present.
- [x] 2.2 Distinguish transient worker calls (drained by close) from escaped idle-stream
      leases (still defer teardown): `close()` waits only for in-flight `open()`/`read()`
      calls, not for the caller to close streams that escaped the reader.
- [x] 2.3 Preserve idempotent `close()`/`__exit__`, one-shot teardown, the `ExceptionGroup`
      on dual inner-close/teardown failure, and post-close `ArchiveyUsageError` for new
      operations.
- [x] 2.4 Document that `close()` blocks until worker calls return: a worker that never
      returns is a caller bug (same as any lock), so no artificial timeout is added — state
      this explicitly in the docstring.

## 3. Boundaries

- [x] 3.1 Keep distinct reader-wide passes (`__iter__` / `stream_members` / `extract_all`)
      single-owner: overlap by a *different* pass still raises `ArchiveyUsageError`.
- [x] 3.2 Do not add per-stream locking; same-stream concurrent access stays caller
      responsibility (standard file semantics).

## 4. Tests

- [x] 4.1 Multi-thread: N threads first-touch `open()` on an unmaterialized `CONCURRENT`
      reader all succeed with correct bytes; assert materialization ran exactly once (spy on
      the member scan).
- [x] 4.2 Multi-thread: first-touch materialization failure wakes every waiter with the
      translated error / clean retry and publishes no partial snapshot.
- [x] 4.3 Multi-thread: `close()` during in-flight reads drains then closes; escaped streams
      stay readable to EOF; concurrent double-`close()` is idempotent with correct
      exception grouping on failure.
- [x] 4.4 Mark the new tests `concurrent_reader` so the Linux `3.13t`
      `free-threaded-concurrency` job exercises them.
- [x] 4.5 Update the thread-safety contract in `packaging-and-extras`, the `MemberStreams`
      docstring, and `docs/parallel-reader.md`: first-touch materialization and `close()`
      are now coordinated; distinct passes and shared streams remain single-owner.

## 5. Verify

- [x] 5.1 `openspec validate --strict reader-concurrency-coordination`.
- [x] 5.2 Full suite in all three dependency configurations (`all`, `all-lowest`,
      `core-only`) plus the `3.13t` marked run; `ruff` / `pyrefly` / `ty` clean.
