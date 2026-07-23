# pyppmd upstream report — 1.3.x native heap corruption on valid PPMd7 data

Status: **not yet filed** (no matching issue at
<https://github.com/miurahr/pyppmd/issues> as of 2026-07-23).

> **Consolidated 2026-07-23.** The canonical, ready-to-file report now lives in
> [`ppmd-native-investigation-results.md` §J](ppmd-native-investigation-results.md)
> — it is self-contained (title, summary, reproduction, root cause, fixes,
> verification checklist) and paste-able into a GitHub issue. That investigation
> **corrected the root cause** of this same defect: the first corrupting write is a
> **use-after-free of the decode output buffer** by the worker thread
> (`ThreadDecoder.c:134`, on a block freed by `OutputBuffer_Finish` /
> `_ppmdmodule.c:552`) — **not** the vendored 7-Zip model being walked on a
> desynchronised range coder, as the earlier draft below hypothesised. The rest of
> the earlier analysis (the 1.3.0 `#126` ThreadDecoder rewrite removing the
> input-empty stop, the `INT_MAX` budget, the missing after-eof guard in the C
> extension, the `Ppmd7T_Free` teardown race) stands and is folded into §J.

File it against [miurahr/pyppmd](https://github.com/miurahr/pyppmd) using §J, and
attach both repro scripts:

- `scripts/pyppmd_crash_repro.py` — probabilistic crash-rate (self-contained:
  `pyppmd` + stdlib), modes `overshoot` / `oversized` / `extra-null` / `sized-safe`.
- `scripts/ppmd_uaf_valgrind.py` — **deterministic** valgrind memcheck gate that
  pins the `Invalid write … ThreadDecoder.c:134` context every run (the evidence a
  fixed release must clear, and the check CI should gate on before retiring
  `--allow-exit-after-green`).

Archivey context (what we ship regardless of the upstream fix — bounded decodes,
`unpack_size`/`pack_size` requirement, single capped NUL, and the
`quiesce-on-close` teardown fix) lives in `docs/internal/known-issues.md` →
“Intermittent `pyppmd` native aborts” and in `ppmd-native-investigation-results.md`
§I.

---

## Crash-shape reference (still valid)

Measured with `scripts/pyppmd_crash_repro.py` (fresh subprocess children, ~5
encode/decode cycles each) on pyppmd 1.3.1 (Linux, x86_64):

| mode | pattern | 1.3.1 |
|------|---------|-------|
| `extra-null` | sized decode to `eof`, then `decode(b"\0", -1)` | crashes (~30–40%) |
| `overshoot` | single `decode(packed, -1)` | crashes (~15–25%) |
| `oversized` | single **sized** `decode(packed, len(data) + 65536)` — no `-1` | crashes (~50–65%) |
| `sized-safe` | `decode(packed, len(data))` only | **0%** (control) |

The `oversized` row is the sharpest datapoint: the crash does **not** require the
`-1` sentinel. Requesting materially more output than the stream's true remaining
payload is what corrupts the heap; the exact remaining size is the safe contract.
1.1.1 / 1.2.0 do not crash on these inputs (they have a different bug — short
output on chunked bounded decodes, which `#126` was fixing).

## Verification checklist for a fixed release

All must be **0 crashes / 0 memcheck errors** (see §J for the same list):

```bash
python scripts/pyppmd_crash_repro.py 50 --mode extra-null
python scripts/pyppmd_crash_repro.py 50 --mode overshoot
python scripts/pyppmd_crash_repro.py 50 --mode oversized
python scripts/pyppmd_crash_repro.py 50 --mode warmup-overshoot
python scripts/ppmd_uaf_valgrind.py --scenario all --strict-pyppmd
uv run --no-sync pytest tests/test_ppmd_raw_streams.py -q
```
