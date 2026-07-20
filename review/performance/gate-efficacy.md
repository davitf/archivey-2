# Gate efficacy — does the benchmark gate enforce the VISION budget?

VISION defines the budget on three axes: wall time (≤1.3×/~2×), bytes decompressed,
and seek patterns, with the solid re-read as the named failure. The machinery is
`benchmarks/harness.py` (+ `baselines/structural.json`), the `benchmark` job in
`ci.yml` (PR-blocking, `--mode structural --scale ci`), and the change-guarded
nightly `benchmark-wall.yml` (`--mode full --scale realistic`, relative wall-ratio
drift gate; absolute VISION bands informational).

Verdict up front: **the structural gate reliably catches the sequential solid
path collapsing to per-member re-decode (and related byte/seek regressions).
Wall ratios are enforced on nightly as *drift vs the previous measurement*, not
as absolute ≤1.3×.** Absolute VISION / Q1 listing bands remain informational
prints (shared-runner noise).

> **Status update (#139 merged, verified; wall drift 2026-07-20):** G3, G4, and
> G5 are closed — `SOLID_DECODE_FACTOR` 2.0 → 1.25, a non-solid over-decode bound
> (×1.1) on `read_all`, seek slack `baseline×2+8` → `baseline+8`, and
> `sevenzip_solid_random` bounded vs its committed baseline ×1.5 (ci scale).
> All three `repro.py` probes now report CAUGHT. **G1 is closed as Q2 option (a)**
> — nightly wall-ratio drift vs previous JSON (`--wall-drift-baseline`), with
> skip re-publish + ≥30d forced re-measure. Absolute ≤1.3× stays informational.
> G6/G7 are partial — #139 added ZIP `open_list`/`extract` stdlib peers, and the
> Q1 direction (2026-07-18) adds listing-ratio peers (`zipfile`/`tarfile` bands,
> `py7zr`/`rarfile` parity) to the missing list.

## G1 — Wall budget: nightly relative drift (done — Q2 (a))

- PR gate: `--mode structural` computes wall ratios but never checks them
  (`_wall_checks` is only called for `--mode full`). Absolute ≤1.3× stays off
  the PR path by design (shared-runner noise).
- Nightly expensive run (`benchmark-wall.yml`): hard-fails on
  `WALL_RATIO_BUDGET = 10.0` **or** on wall-ratio *drift* vs the previous
  successful artifact (`WALL_RATIO_DRIFT_FACTOR` / `WALL_RATIO_DRIFT_MIN_ABS`).
  Absolute VISION / Q1 listing lines remain `VISION BUDGET (informational)`
  prints only.
- Quiet days **re-publish** the previous JSON (preserving `measured_at`,
  stamping `republished_at`) so dormant cron successes still carry an artifact
  for the next compare. A full re-measure is forced when `measured_at` is older
  than ~30 days (runner / toolchain drift), independent of HEAD age.
- `workflow_dispatch` + `skip_drift=true` re-seeds after an intentional
  slowdown. An explicit `--wall-drift-baseline` path fails closed if the file
  is missing or has no overlapping `wall_ratio` cases.

See `QUESTIONS.md` Q2 (decided 2026-07-20) and debt-ledger Q1.

## G2 — The canonical O(n²) collapse IS caught (fine)

`repro.py` probe 1 replaces `SevenZipReader._iter_with_data` with the base-class
default, so the "sequential" benchmark degenerates to per-member from-start folder
decodes — the exact VISION trap. The gate fails on both axes:

```
sevenzip_solid_sequential: bytes_decompressed=34603008 > unpacked×2.0=4194304
sevenzip_solid_sequential: source_seek_count=35 > bound 16
```

This is the one place the structural gate does exactly what VISION asks.

## G3 — A clean 2× solid re-decode passes (blocker-adjacent)

`SOLID_DECODE_FACTOR = 2.0` with a non-strict bound (`harness.py:526-532`:
`bytes > unpacked * 2.0` fails, `==` passes). A regression that decodes every solid
folder exactly twice — e.g. an eager verify pass before the real read — sits
precisely on the bound and passes (`repro.py` probe 2). VISION: "*An implementation
that re-reads a solid block fails the benchmark even if a small test corpus hides
it.*" — a single full re-read does not fail this benchmark.

The ×2 slack is documented as "arbitrary corpora" headroom (`harness.py:49-51`),
but the harness only ever runs its own generated fixtures, and the unit-level
decode-once test already holds ×1.1 on controlled fixtures
(`test_measurement.py:30`). Recommendation: drop the harness factor to ~1.25 (Q3).

## G4 — Non-solid re-decompression is invisible (blocker)

The byte axis for ZIP/TAR/gzip counts bytes at the *delivered member output*
(`_wrap_member_stream(track_output=True)` → `OutputCountingStream`), which the
harness itself calls tautological (`harness.py:10-15,533-545` — only an
under-decode guard). Consequences, demonstrated by `repro.py` probe 3
(`ZipReader._open_member` patched to open, fully consume, close, then re-open each
member — i.e. every member decompressed twice, delivered once):

- bytes_decompressed doubles but only a `<` unpacked check exists — passes;
- source seeks grow but stay inside the `baseline×2 + 8` slack
  (`harness.py:551`) — passes;
- wall time roughly doubles — unasserted (G1).

Result: `no gate failure`. A 2× CPU regression on the most common format merges
green through the PR gate. Fixes that would make this visible: count decode-layer
output (like the 7z folder path) instead of delivered output for the common
formats; and/or assert `compressed_bytes_consumed ≈ archive size` per case; and/or
tighten the ci-scale seek bound to equality against the committed baseline (the
fixtures are deterministic — observed seeks reproduce exactly across runs).

## G5 — The random-open O(n²) is recorded, deliberately ungated

`sevenzip_solid_random` notes "re-decode recorded; not gated" (`harness.py:478`,
baseline records 16.5× at ci scale / 32.5× at realistic). For the *random-access*
workload this cost is inherent to solid formats and the cost model flags it
(`AccessCost.SOLID`), so not gating the absolute value is defensible — but nothing
bounds it against *getting worse* (e.g. a folder-cache regression turning 16.5×
into 33×; the seek axis would not move). A cheap gate: bound the case's
bytes_decompressed to its committed baseline ×1.5, same shape as the seek check.
Note the user-facing paths do NOT hit this trap (CLI `test`/full `extract` are
decode-once — `SUMMARY.md`), with the one exception documented in `hotspots.md` H1.

## G6 — Baseline meaningfulness and measurement blind spots

- **Seek bounds are loose:** `baseline×2 + 8` lets an 8-member ZIP double its
  per-member seeks and still pass; fixtures are deterministic, so equality (or a
  small +k) would hold and catch churn G4-style.
- **`--update-baselines` is self-certifying:** a PR that regresses seeks can ship
  the regressed baseline in the same diff; nothing diffs baselines semantically.
- **No RAR in the committed baseline** (`structural.json` has no `rar_*` cases —
  the CI runner lacks the `rar` writer, so those harness cases never run in CI).
  The RAR decode-once unit test runs on committed fixtures, but:
- **The RAR byte axis cannot see solid rewind:** bytes are counted on the `unrar p`
  pipe output, which emits only the requested member — an internal solid re-decode
  by unrar is invisible by construction (documented in
  `test_measurement.py:166-169`). Seeks don't help (no archivey-side source
  seeks). RAR decode-once is therefore *asserted about the pipe protocol*, not
  measured about work done.
- **7z password confirmation decodes whole folders uncounted:**
  `_password_for_folder` runs `open_folder_pipeline` without `_track_decompressed`
  (`sevenzip_reader.py:550-573`), so an encrypted-solid benchmark case would
  under-report. No current case is encrypted — worth remembering when adding one.

## G7 — Coverage: what has no benchmark at all

Confirmed absent from `run_cases` (`harness.py:257-514`) and the committed baseline:

| Path | Status |
|------|--------|
| `open` / `list` vs stdlib peer | wall never compared (only `read_all` gets `stdlib_wall_s`) — how the 5–8× open+list miss stayed invisible |
| `extract` vs stdlib peer | measured structurally, never against `zipfile.extractall`/`tarfile.extractall` — how the 2.4–3.7× extract miss stayed invisible |
| RAR read via `unrar` pipe | no CI case (G6) |
| ZIP AES / native-codec members (#106) | none |
| Accelerated deflate/zlib *inside ZIP members* (#105) | none — accel cases are single-stream `.tar.gz`/`.tar.bz2` only; the per-member AUTO gate behaviour (`hotspots.md` H5) is untested by the harness |
| ISO | non-gating side script only (`tar_iso_lock_baseline.py`, documented choice) |
| Encrypted / VerifyingStream-heavy paths | none |

Suggested minimum additions before 0.2.0: stdlib peers for open+list and extract
(both cheap), and one RAR read case gated on committed fixtures + `unrar`
availability.
