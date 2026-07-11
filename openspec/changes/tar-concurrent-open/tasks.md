## 1. Locked member-stream primitive

- [x] 1.1 Add a `streamtools` wrapper that delegates to an inner `BinaryIO` and holds a
      caller-supplied lock across `read`, `readinto`, supported `seek`/`tell`, and inner
      close/context exit (plus every related method audit shows can touch the shared handle);
      preserve normal `io.UnsupportedOperation` for unsupported positioning
- [x] 1.2 Unit-test two fake seek-before-read streams sharing one handle/lock:
      single-thread and threaded interleave, readinto, seek/tell, close-vs-operation
- [x] 1.3 Re-export from `streamtools` as appropriate
- [x] 1.4 Ensure raw exceptions are captured under the lock but translated/stamped/logged
      only after release; no provider/callback/finalizer/lifecycle hook runs under the helper
      lock, while unavoidable library-internal decode may remain within the atomic handle call
- [x] 1.5 Coordinate with `ArchiveStream` claim/call/publish: lazy `open_fn` and inner close
      run after releasing stream-state, and lifecycle release runs after the backend lock

## 2. Wire TAR-RA and ISO

- [x] 2.1 Create the `TarReader` lock before opening tarfile; use it for `tarfile.open`,
      `getmembers()` (verified to call `_load()` / `next()` seek/tell/read), direct strict-EOF
      `TarFile.fileobj.read()`, `extractfile`, every wrapped member operation, any other
      audited shared-fileobj access, and `_tar.close()`
- [x] 2.2 Create the `IsoReader` lock before opening pycdlib; use it for `PyCdlib.open` /
      `open_fp`, `open_file_from_iso`, `PyCdlibIO.__enter__`, every wrapped member operation,
      any other audited `PyCdlib._cdfp` / `PyCdlibIO._fp` access, and `_iso.close()`
- [x] 2.3 Place the locked layer below archivey buffering/error/lifecycle wrappers so no
      refill or delegated seek/tell bypasses it
- [x] 2.4 Close inner member/archive library resources under the backend lock, release it,
      then translate/log/release lifecycle leases (never backend lock → lifecycle lock)
- [x] 2.5 Leave the streaming TAR public contract unchanged and exclusive
- [x] 2.6 Use the same lock for streaming TAR initialization, iterator/shared-handle calls,
      `extractfile`, yielded-stream operations, EOF verification, and close; it remains
      exclusive and the lock is normally uncontended
- [x] 2.7 Record the pinned pycdlib audit: `walk()` / `get_record()` traverse in-memory parsed
      records under materialization ownership and do not touch `_cdfp`; add a regression probe
      and lock the complete call if any supported version gains handle access
- [x] 2.8 Audit all remaining tarfile/pycdlib shared-handle operations and record complete
      coverage in code comments/design; do not assume `read()` is the only repositioning call

## 3. Specs and docs

- [x] 3.1 Land after / with `concurrent-member-streams` (it owns the cross-format worker/
      lifecycle contract); apply this change's `format-tar` and `format-iso` deltas
- [x] 3.2 Ensure TAR-RA and ISO support simultaneous random-access streams under declared
      `MemberStreams.CONCURRENT`, with no TAR/ISO-specific exemption; the lock mechanism is
      not instantiated for undeclared (default single-stream) readers
- [x] 3.3 Update `docs/parallel-reader.md` TAR-RA/ISO rows with comprehensive shared-handle
      lock coverage (`getmembers`/EOF and pycdlib catalog audit included), callback boundary,
      capability-conditional positioning, and correctness-vs-parallelism trade-off
- [x] 3.4 Update relevant ABC/backend docstrings to point to the one-lock mechanism and
      claim/call/publish non-nesting rules; cross-format lifecycle wording remains owned by
      `concurrent-member-streams`

## 4. Tests

- [ ] 4.1 **DEFERRED (post-v1 / D15 with `CONCURRENT` promotion) — reminder.** Interleaved
      and multi-thread open/read/readinto/close plus supported positioning for plain TAR-RA.
      v1 ships the lock + cooperative single-thread coverage from `concurrent-member-streams`.
- [ ] 4.2 **DEFERRED (post-v1 / D15) — reminder.** Multi-thread operations for compressed
      TAR-RA (at least `.tar.gz`).
- [x] 4.3 Sparse TAR member still expands correctly (fixture or skip if none)
- [ ] 4.4 **DEFERRED (post-v1 / D15) — reminder.** Interleaved and multi-thread open/read/
      readinto/close plus supported positioning for ISO, including concurrent member
      initialization/context entry.
- [x] 4.5 Sequential extract regression for TAR and ISO (uncontended lock path)
- [x] 4.6 Streaming TAR existing tests still pass
- [ ] 4.7 **DEFERRED (post-v1 / D15) — reminder.** Forced race tests prove archive close/
      member close cannot interrupt an active shared-handle operation under the supported
      lifecycle sequence.
- [ ] 4.8 **DEFERRED (post-v1 / D15) — reminder.** Reentrant logging/error/lifecycle probes
      prove no callback executes under the backend lock and no lock-order deadlock occurs.
- [x] 4.9 Forced-interleaving probes cover TAR `getmembers()` and direct EOF reads; pinned-
      pycdlib probes assert `walk()` / `get_record()` do not access `_cdfp`
      (**cooperative / single-thread probes in v1**; multi-thread stress deferred with 4.1)
- [x] 4.10 Capability tests assert seek/tell correctness only where supported and normal
      `io.UnsupportedOperation` otherwise

## 5. Measurement

- [ ] 5.1 **DEFERRED (post-v1 with performance claims) — reminder.** Record representative
      plain TAR, compressed TAR, and ISO baseline wall time and lock wait/hold time, plus
      seek count and bytes decompressed/read where practical; impose no pass/fail speed
      threshold. Companion: `concurrent-member-streams` task 7.9.
- [ ] 5.2 **DEFERRED — reminder.** For a later independent-handle/raw-extent/native-reader
      optimization, collect targeted before/after metrics for changed resources (peak memory
      only if buffering can change) before making a throughput claim.

## 6. Verification

- [ ] 6.1 `openspec validate --strict tar-concurrent-open`
- [ ] 6.2 `uv run --no-sync ruff check` on touched paths
- [ ] 6.3 `uv run --no-sync pyrefly check` and `uv run --no-sync ty check` clean
- [ ] 6.4 `uv run --no-sync pytest` for TAR / ISO / streamtools tests (full three-config
      gate before push per CONTRIBUTING)
