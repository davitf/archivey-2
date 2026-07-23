## 1. Safety net (T1)

- [x] 1.1 Extend `tests/test_mutation_fuzz.py` with static solid RAR4/RAR5 fixtures
      (`basic_solid__.rar`, `basic_solid__rar4.rar`); reuse `_exercise` / mutation kinds;
      skip cleanly when `unrar` is unavailable.
- [x] 1.2 Confirm solid demux path is exercised under at least truncate + bitflip kinds
      (smoke locally with default `_N`).

## 2. S2 — one finalize path

- [x] 2.1 Collapse `_finalize_materialized_links` / `_finalize_pass_links` into one
      helper with explicit double-fault policy (`error is not None` → swallow secondary
      Corruption/Truncated; clean EOF → re-raise). Scope note: "one helper" unifies the
      *double-fault policy and guard prose*, not necessarily byte-identical behavior —
      per design Decision 4, preserve each path's existing `is_current`/link-resolve
      ordering (a branch inside the shared helper is acceptable) unless a failing test
      forces convergence. Don't reorder eager vs progressive finalize on faith.
- [x] 2.2 Point eager materialization and `_ProgressivePassIterator` at the shared
      finalizer; delete mirrored guard comments.

## 3. S3 — one pass-stream driver

- [x] 3.1 Add shared driver helper on `BaseArchiveReader` (`close_previous` / open hook /
      resource `finally`). The driver **always closes the last stream in its own `finally`**
      — no `leave_last_open` flag (design Decision 3): drop base's leave-open special case
      and its `base_reader.py:493-495` comment. **`finally` order:** close last stream,
      *then* resource cleanup (solid / clear `_live_unrar`) — same as today’s 7z/RAR;
      never tear down the block/pipe under a live member wrapper.
- [x] 3.2 Rewrite base / TAR streaming / 7z / RAR solid `_iter_with_data` to use the
      driver (TAR: `close_previous=False`; 7z/RAR solid: add resource-cleanup hook). Rely on
      idempotent `ArchiveStream.close` for the driver `finally` + `stream_members` `finally`
      double-close. Module paths live under `internal/backends/`
      (`sevenzip_reader.py`, `rar_reader.py`).

## 4. Triage docs

- [x] 4.1 Record debt-ledger Q3 = (b) pay-now in `QUESTIONS.md` / `STATUS.md` /
      `SUMMARY.md`; do not add PLAN/IDEAS entry-gate language.

## 5. Verify

- [x] 5.1 Targeted: mutation solid-RAR slice; `test_rar_reader` solid; `test_sevenzip_reader` solid;
      `test_measurement` solid decode-once; TAR progressive/materialize; double-fault
      (`test_reader_contract`); `stream_members` close/ownership cooperative tests.
- [x] 5.2 Full suite in `[all]` (and note `[core-only]` / `[all-lowest]` before merge).
- [x] 5.3 `openspec validate --strict unify-pass-driver`
- [x] 5.4 `ruff format` / `ruff check` / `pyrefly check` / `ty check` clean on touched paths
