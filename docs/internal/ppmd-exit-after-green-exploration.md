# Exploration: `pyppmd` exit-after-green abort

**PR:** #188 (this branch)  
**Issue:** `docs/internal/known-issues.md` → “`pyppmd` exit-after-green abort”  
**Status:** investigation in progress  
**Date started:** 2026-07-23  

This doc is a live lab notebook. Findings are appended as experiments finish so
reviewers can read updated versions mid-investigation.

---

## Goal

Isolate whether the intermittent SIGSEGV / `corrupted size vs. prev_size` on
interpreter teardown after a green `tests/test_ppmd_raw_streams.py` session is:

1. **pure `pyppmd`** (teardown / worker-thread / object lifetime), or
2. an **interaction** with other extension modules loaded at import time
   (`rapidgzip`, `lz4`, `brotli`, `zstd` / `backports.zstd`, …), or
3. triggered only by **adversarial** tests (truncated / hostile-tail / early-close),
   or
4. a dangerous **archivey usage pattern** we can avoid (like the previous
   unbounded `decode(..., -1)` / overshoot mitigation).

Success criteria for a “fix”: reproducible local rate drops to ~0 under a
documented soak, ideally with a minimal bare-`pyppmd` (or single-culprit) repro,
then we can remove `--allow-exit-after-green` from required CI.

---

## Background (from known-issues + prior work)

### Two different fingerprints

| Fingerprint | When | Mitigated? |
|-------------|------|------------|
| Mid-decode / `warmup_codecs` / unbounded `max_length` | During `decode` on valid or overshot streams | Yes — bound every decode; refuse unsized PPMd7 |
| **Exit-after-green** (this doc) | After `sessionfinish exit=0`, during teardown / GC | Open |

Dying children still list `pyppmd.c._ppmd`, **`rapidgzip`**, lz4, brotli
(sometimes zstd) even when no accelerator *tests* ran — because
`archivey.internal.streams.codecs` does `_optional("rapidgzip")` etc. at
**import** time, and `test_ppmd_raw_streams` imports `open_codec_stream` from
that module.

### How archivey currently uses pyppmd (already hardened)

`PpmdDecoder` in `src/archivey/internal/streams/decompress.py`:

- Never passes `max_length=-1` to the native decoder (sized remaining, or
  bounded 64 KiB chunks for unsized PPMd8).
- Requires `unpack_size` for PPMd7.
- Flush injects at most one documented extra NUL, bounded by remaining size.
- After `eof` + unbounded would have been the crashy path — guarded.

So exit-after-green is **not** the same as “we forgot to bound again” unless a
test still reaches a bad call somehow (spy test asserts `length >= 0`).

---

## Experiment matrix (planned)

| # | Env / filter | What it tells us |
|---|--------------|------------------|
| E0 | `[all]` baseline soak `--repeat 20` | Confirm local rate on this machine |
| E1 | Uninstall rapidgzip, lz4, brotli, backports.zstd, inflate64, bcj; keep pyppmd + pytest | Isolates “pyppmd + archivey + pytest” without other natives |
| E2 | If E1 clean: add rapidgzip alone, re-soak | rapidgzip interaction? |
| E3 | If E1 clean: add lz4 / brotli / zstd separately | Which friend poisons? |
| E4 | Bare script: only `pyppmd`, mimic test loops (valid + truncated + early destroy) | Pure pyppmd teardown? |
| E5 | Subset filters: only happy-path vs only adversarial tests | Corruption-only? |
| E6 | One-test-per-subprocess / reverse order | Narrow poison test |
| E7 | Memory tracers (`PYTHONMALLOC=debug`, `MALLOC_CHECK_=3`, faulthandler, optional Valgrind if feasible) | When heap first goes bad |

Commands (canonical soak):

```bash
PYTHONFAULTHANDLER=1 uv run --no-sync python scripts/ci_run_native_modules.py \
  --modules tests/test_ppmd_raw_streams.py --repeat 20
```

---

## Log

### 2026-07-23 — setup

- Checked out PR branch `cursor/ppmd-exit-after-green-known-issue-3429`.
- Everyday env: `pyppmd 1.3.1`, `rapidgzip 0.16.0`, uv CPython 3.11.
- Created this exploration doc; starting E0 + preparing E1 env.

### Results (fill as runs complete)

*(pending)*
