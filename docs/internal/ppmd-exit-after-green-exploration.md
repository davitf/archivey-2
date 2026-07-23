# Exploration: `pyppmd` exit-after-green abort

**PR:** #188 (this branch)  
**Issue:** `docs/internal/known-issues.md` → “`pyppmd` exit-after-green abort”  
**Status:** mitigated (see “Fix applied” below)  
**Date started:** 2026-07-23  
**Date closed:** 2026-07-23

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

#### E0 — `[all]` baseline (`--repeat 20`)

- **Rate: 1/20** SIGSEGV (rc `-11`) on iter 7.
- All green sessions: `19 passed`, breadcrumb
  `sessionfinish exit=0 …::test_archivey_ppmd7_requires_unpack_size`.
- Fatal site (not the poison call — late tripwire):

  ```
  Fatal Python error: Segmentation fault
  Garbage-collecting
    _pytest/unraisableexception.py → gc_collect_harder → cleanup
    → _ensure_unconfigure / wrap_session
  Extension modules: backports.zstd._zstd, lz4._version, lz4.frame._frame,
    _brotli, pyppmd.c._ppmd, rapidgzip (total: 6)
  ```

- Confirms the fingerprint is reproducible on this Cloud agent host
  (uv CPython 3.11.15, glibc 2.39).

#### E1 — pyppmd-only venv (`--repeat 40`)

- Dedicated `/tmp/ppmd-only-venv`: editable archivey + `pyppmd==1.3.1` +
  pytest/pytest-timeout only. Confirmed absent: rapidgzip, lz4, brotli,
  backports.zstd, inflate64, bcj, py7zr. Only extension at crash:
  `pyppmd.c._ppmd`.
- **Rate: 31/40 FAIL** (SIGSEGV/SIGABRT) — *higher* than `[all]`, not lower.
- Breadcrumb almost always:
  `start …::test_ppmd_decoder_truncated_flush_reports_unfinished`
  (12 tests passed → crash entering / during the truncated-flush test).
- **Conclusion:** not an interaction with rapidgzip/lz4/brotli/zstd. Those
  modules in the fatal list were red herrings (import-time loads). Pure
  `pyppmd` (+ archivey’s truncated flush pattern).

#### E5a — happy-path only (`-k 'not truncated and not hostile and not early_close'`)

- pyppmd-only venv, 40 repeats: **0/40 failures**.

#### E5b — adversarial only (`-k 'truncated or hostile or early_close'`)

- pyppmd-only venv, 40 repeats: **31/40 failures** (matches E1).

#### E4 — bare `pyppmd` (no archivey)

| Mode | Rate |
|------|------|
| Sized full decode (control) | **0/40** |
| Underfed half-input then `del` (no NUL flush) | **0/40** |
| Early partial decode then `del` ×5 | **0/40** |
| Half-input then `decode(b"\0", remaining)` ← archivey flush shape | **35/40**, later **85/100** |

Fatal frame is inside the `decode(b"\0", remaining)` call (not only teardown).
`remaining` after half-feed of the raw-streams fixture is ~1736.

Cap A/B on that bare shape (100 children):

| NUL `max_length` cap | fail/100 |
|----------------------|----------|
| full remaining (~1736) | 85 |
| 64 | 0 |
| 1 | 0 |

### Root cause (confirmed)

**Dangerous archivey pattern (same family as the prior unbounded/`-1` bug):**

`PpmdDecoder.flush()` injects one documented extra NUL with
`max_length = remaining unpack_size`. That is correct for a *true* compressed
EOF where the encoder omitted a trailing null (remaining tail is tiny). On a
**truncated mid-stream** member, `remaining` is still large, and
`decode(b"\0", large)` asks the 1.3.x worker to emit thousands of symbols past
the real end of stream → heap corruption (sometimes mid-call, sometimes silent
until GC / exit — the “exit-after-green” fingerprint under `[all]`).

Happy-path and underfed-without-NUL-flush do not crash. Other extension modules
are not required.

### Fix applied

1. **Cap extra-NUL recovery** (`_PPMD_EXTRA_NUL_MAX_OUTPUT = 64`) in
   `PpmdDecoder.flush` and empty-`feed` NUL injection
   (`src/archivey/internal/streams/decompress.py`).
2. **Subprocess-isolate** unfinished-decoder adversarial tests in
   `tests/test_ppmd_raw_streams.py` (avoids in-process `Ppmd7T_Free` race
   poisoning the parent session).
3. Remove `--allow-exit-after-green` from required CI for this module.

### Post-fix soaks

| Env | Rate |
|-----|------|
| `[all]` `--repeat 100` | **0/100** |
| pyppmd-only `--repeat 100` | **0/100** |

(NUL-cap alone: `[all]` ~0–1/40, pyppmd-only still ~6/100 exit-after-green from
Free race until subprocess isolation.)

### Answers to the original questions

- **rapidgzip / lz4 / brotli interaction?** No — pyppmd-only crashed *more*.
- **Dangerous archivey pattern?** Yes — large-budget NUL flush on truncated
  streams (same family as the prior unbounded/`-1` mitigation).
- **Only corrupted/truncated?** Happy-path alone was clean; truncated flush was
  the high-rate trigger. Unfinished-decoder Free is a secondary residual on
  truncated/early-close teardown.
- **Upstream?** Overshoot + `Ppmd7T_Free` remain pyppmd 1.3.x defects; archivey
  avoids the patterns that trip them in-process.

### Follow-up measurements (2026-07-23): why 64, and how much does NUL emit?

**How 64 was chosen originally:** not from measuring legitimate recovery size.
It was the first round-number that stayed green in a half-truncated
`decode(b"\0", min(rem, cap))` soak (cap≤512 → 0/30–0/100; cap≥1024 often
native-aborted). So it is a **crash-threshold cushion**, not a derived bound
from “bytes needed at true EOF”.

**Complete / well-formed streams (pyppmd 1.3.1 + encoder `flush()`):**

| Probe | Result |
|-------|--------|
| ~10k samples (varied payloads, chunk sizes, trailing `\\0`) | **0** cases where `needs_input` after full packed feed |
| Docs-style sample (`decode(packed, n)` then maybe `decode(b"\\0", rem)`) | **0** NUL recoveries in 1625 tries |
| py7zr `PpmdCompressor`/`PpmdDecompressor` same shapes | **0** shortfalls |
| Trailing-NUL payloads only (`b"a"*n + b"\\0"`, n=1..200) | **0** hits |

So on this wheel/version with a proper encoder flush, the documented “encoder
omits a last null” path **does not fire**. We therefore **cannot empirically
prove** that 64 is always enough for a true missing-NUL completion — we never
observe one. If that path ever yields more than 64 remaining output bytes from
a single extra NUL, a capped flush would under-read and surface
`TruncatedError` (safe failure, not a crash). The pyppmd METADATA sample still
documents the quirk and passes full `length - len(result)` as the budget.

**Truncated streams (fixture `CONTENT` 1760 B → 57 B packed; subprocess-isolated):**

When a mid-cut still has `needs_input` and we feed one NUL:

- Successful calls almost always return **1–3 output bytes** (p50=1, p90=2,
  p99=3, max=3 across non-crash truncated soaks), **not** kilobytes of garbage.
- Uncapped `max_length=remaining` (~1700) often **SIGSEGV/SIGABRT instead of
  filling the buffer** — the large budget is the crash, not a large garbled
  return. Example earlier: half-cut uncapped **85/100** children aborted.
- Those 1–3 bytes sometimes bitwise-match the true payload prefix at that
  offset (coincidence / local model state); they never complete the member
  (`rem` stays huge; **0** full recoveries).
- Near-complete cuts (`eof=True`, short output) skip the NUL path entirely;
  damage there is the earlier oversize `decode(truncated, unpack_size)` /
  `Ppmd7T_Free` story, not NUL emit size.

**Implication for the constant:** observed successful NUL emits are ≤3 bytes,
so **1 or 8 would match the data** more tightly than 64. Keeping 64 is a
deliberate cushion for a possible multi-byte docs-path recovery we could not
reproduce on 1.3.1. Tightening is reasonable if we want least privilege; it is
not required for the crash mitigation already measured at 0/100.
