# Performance review — SUMMARY

**Brief:** `brief.md` (VISION claim #4: ≤1.3× stdlib wall-time on common paths, ~2×
where safety justifies it; benchmark suite must track bytes-decompressed and seek
patterns, and "an implementation that re-reads a solid block fails the benchmark").

**Reviewed at:** `main` @ `7139c13` (CLI #120 merged), branch
`claude/performance-brief-review-176s93`.
**Baseline:** `[all]` config — 1699 passed / 131 skipped, `ruff` / `pyrefly` / `ty`
clean, committed structural gate green (exit 0), realistic harness green (exit 0).
**Host:** 4-core x86_64 KVM, Linux 6.18, CPython 3.11. Wall ratios are directional
(shared runner); every ratio below was stable across ≥2 independent runs.
All measurements reproduce with `[all]`; the accelerator findings need rapidgzip
(`[seekable]`), the 7z fixture builder needs py7zr. Repro scripts:
`repro.py` (gate probes), `measurements.py` (all cited numbers).

## Headline

**The ≤1.3× budget is currently neither met nor enforced.** On this host the ZIP
common paths run at 2.2–2.3× (read-all), 2.4–3.7× (extract-all) and 5–8×
(open+list) stdlib — all above even the 2× safety band — and no CI job can notice:
wall ratios are asserted nowhere (the PR gate is structural-only; the nightly's only
hard wall check is a 10× sanity ceiling, the VISION band is an informational print),
the byte axis is tautological for non-solid formats, and `open`/`list`/`extract`
have no stdlib peer in the harness at all. Separately, the one user-facing path that
*does* hit the solid re-decode trap end-to-end is selective extraction: extracting
one early member of a solid 7z folder decodes ~the whole folder (31× the needed
bytes on the review fixture) because the sequential pass eagerly positions every
member and extraction never stops early.

What holds up well: the canonical VISION trap is protected where it matters — CLI
`list`/`test`/`extract`(all) on solid 7z are exactly decode-once, the structural
gate *does* catch an O(n²) collapse of the sequential path (both byte and seek
axes), the cost receipts are honest, the rapidgzip AUTO gate prevents a real 5×
many-small pessimization at sequential parity, and the #128 memory fix is confirmed
bounded.

## Top findings

| # | Severity | Finding | Where | Status |
|---|----------|---------|-------|--------|
| P1 | **blocker** | ≤1.3× wall budget enforced nowhere; nightly hard-fails only at 10×, VISION band informational | `benchmarks/harness.py:55,826-834`, `benchmark-wall.yml` | open — decision needed (Q2) |
| P2 | **blocker** | Budget not met: ZIP read-all 2.2–2.3×, extract-all 2.4–3.7×, open+list 5–8×; TAR read 1.8× | `budget-table.md` | open |
| P3 | **blocker** | Selective solid-7z extraction decodes ~whole folder for one early member (31× needed bytes); CLI `extract archive.7z <name>` hits it | `sevenzip_reader.py:283-323`, `extraction.py:340` | open — fix proposed (`hotspots.md` H1) |
| P4 | high | Non-solid re-decompression is invisible to the gate: decode-twice-deliver-once ZIP regression passes (byte axis counts delivered output; seek slack ×2+8 absorbs churn; wall ungated) | `gate-efficacy.md` G4, `repro.py` probe 3 | open |
| P5 | high | A full 2× solid re-decode passes the gate (`SOLID_DECODE_FACTOR = 2.0`, non-strict bound) — VISION says a re-read must fail | `harness.py:51,526-532`, `repro.py` probe 2 | open — tighten (Q3) |
| P6 | med | Harness has no stdlib peer for open/list/extract (why P2's extract miss went unnoticed); no RAR case in committed baseline; ZIP-AES / native-codec / in-ZIP-accelerated paths unbenchmarked | `gate-efficacy.md` G6/G7 | open |
| P7 | med | Per-`open()` 5–8× zipfile (detection + member-model build ~0.3 ms/archive) — the founding million-archive sweep pays minutes | `hotspots.md` H3 | follow-up |
| P8 | low | rapidgzip AUTO threshold (1 MiB) conservative: seek workloads win ~1.5× well below it; provenance script never measured compressed sizes near 1 MiB | `hotspots.md` H5 | follow-up |
| P9 | low | Measurement blind spots: 7z password-confirm folder decode uncounted; RAR byte axis (unrar pipe output) cannot see solid rewind | `gate-efficacy.md` G6 | follow-up |

Blocker rationale (per brief): P1/P4/P5 = "gate can't catch a regression"; P2 = "budget
missed"; P3 is the VISION-named trap reachable from the shipped CLI.

## What is actually fine

- **Solid decode-once end-to-end.** CLI `list` decodes 0 bytes, `test` and full
  `extract` decode exactly 1.00× unpacked on solid 7z (`--track-io` evidence). The
  harness sequential invariant is real and additionally pinned per-backend in
  `test_measurement.py` at a tight ×1.1 across the full test matrix.
- **The canonical O(n²) collapse is caught.** Replacing the 7z sequential pass with
  per-member random opens fails the gate on *both* axes (bytes 16.5× > 2×; seeks
  35 > 16) — `repro.py` probe 1.
- **Cost receipts are honest.** 7z: `SOLID` + `solid_block_count=1`; ZIP/TAR:
  `DIRECT`; listing costs match reality (0 bytes decompressed to list ZIP/TAR/7z/gz;
  measured). Random-vs-streaming asymmetry (16.5–32.5× vs 1.0×) is exactly what the
  `SOLID` axis predicts. `reader.read()` of one member costs precisely prefix+member.
- **The AUTO accelerator gate earns its keep.** Forced-ON on a 1000×4 KiB ZIP is
  5.2–5.6× slower than AUTO; at the 1 MiB boundary sequential reads are parity and
  seek workloads win with the accelerator — the gate never pessimizes, it only
  leaves some sub-threshold seek wins unused (P8).
- **#128 (F3) holds.** `read(1)` on an accelerated multi-MiB gzip: peak-RSS delta
  ≈ 0 MiB; a 64 MiB mid-stream seek stays bounded.
- **Old D2 is fixed.** `capture_open_site` is a cheap frame walk retaining only
  `file:line` (`open_site.py:31`); retained memory ≈ 25 KiB per open+listed reader.
- **gzip / tar.bz2 / tar.gz(accel-on) read paths are within budget** (1.02–1.06×,
  1.02×, 0.65–0.73×); tar.gz accel-off sits at the 1.30–1.31× line; measurement
  is genuinely zero-overhead when off (counters None, wrappers identity).
- **Nightly change-guard design** (skip-if-dormant) is sensible and cheap.

## Files

- `gate-efficacy.md` — does the gate enforce the budget (G1–G7, probes).
- `budget-table.md` — archivey-vs-stdlib ratios with attribution.
- `hotspots.md` — attributed hotspots H1–H5 with `ByteCounter`/profile evidence.
- `QUESTIONS.md` — maintainer decisions (budget interpretation, gate policy,
  threshold, verify-skip knob).
- `repro.py`, `measurements.py` — runnable evidence.
