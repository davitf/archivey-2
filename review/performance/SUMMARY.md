# Performance review — SUMMARY

**Brief:** `brief.md` (VISION claim #4: ≤1.3× stdlib wall-time on common paths, ~2×
where safety justifies it; benchmark suite must track bytes-decompressed and seek
patterns, and "an implementation that re-reads a solid block fails the benchmark").

**Reviewed at:** `main` @ `7139c13` (CLI #120 merged), branch
`claude/performance-brief-review-176s93`.
**Post-merge update (2026-07-17):** re-measured at `main` @ `b9cdeac` after #136
(solid lazy open + early exit) and #137 (verify fusion) merged — see
"Post-merge update" below and `residual-gap.md` for the revised attribution.
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
| P1 | **blocker** | ≤1.3× wall budget enforced nowhere; nightly hard-fails only at 10×, VISION band informational | `benchmarks/harness.py:55,826-834`, `benchmark-wall.yml` | **open** — decision needed (Q2) |
| P2 | **blocker** | Budget not met: ZIP read-all 2.2–2.3×, extract-all 2.4–3.7×, open+list 5–8×; TAR read 1.8× | `budget-table.md` | **partial** — #136/#137/#139: large-member ZIP read ≤1.25×, realistic extract ~1.9×; open+list / many-small remain under Q1 |
| P3 | **blocker** | Selective solid-7z extraction decodes ~whole folder for one early member (31× needed bytes); CLI `extract archive.7z <name>` hits it | `sevenzip_reader.py:283-323`, `extraction.py:340` | **fixed by #136** — verified 31.0× → 1.00× |
| P4 | high | Non-solid re-decompression is invisible to the gate: decode-twice-deliver-once ZIP regression passes (byte axis counts delivered output; seek slack ×2+8 absorbs churn; wall ungated) | `gate-efficacy.md` G4, `repro.py` probe 3 | **fixed by #139** — over-decode ×1.1 bound + seek slack baseline+8; probe CAUGHT |
| P5 | high | A full 2× solid re-decode passes the gate (`SOLID_DECODE_FACTOR = 2.0`, non-strict bound) — VISION says a re-read must fail | `harness.py:51,526-532`, `repro.py` probe 2 | **fixed by #139** — factor 1.25; probe CAUGHT |
| P6 | med | Harness has no stdlib peer for open/list/extract (why P2's extract miss went unnoticed); no RAR case in committed baseline; ZIP-AES / native-codec / in-ZIP-accelerated paths unbenchmarked | `gate-efficacy.md` G6/G7 | **partial** — #139 adds ZIP open_list/extract peers; `py7zr`/`rarfile` listing peers + RAR/encrypted/accel still missing |
| P7 | med | Per-`open()` 5–8× zipfile (detection + member-model build ~0.3 ms/archive) — the founding million-archive sweep pays minutes | `hotspots.md` H3 | **partial** — #136 caches extension map; model-build toward 2–3× **actionable** (Q1) |
| P8 | low | rapidgzip AUTO threshold (1 MiB) conservative: seek workloads win ~1.5× well below it; provenance script never measured compressed sizes near 1 MiB | `hotspots.md` H5 | **follow-up** (future) |
| P9 | low | Measurement blind spots: 7z password-confirm folder decode uncounted; RAR byte axis (unrar pipe output) cannot see solid rewind | `gate-efficacy.md` G6 | **follow-up** (future) |

Blocker rationale (per brief): P1/P4/P5 = "gate can't catch a regression"; P2 = "budget
missed"; P3 is the VISION-named trap reachable from the shipped CLI.

## Post-merge update (#136 / #137, `main` @ `b9cdeac`)

Re-verified after both stream-layer PRs merged (full suite green in `[all]`;
selective-solid probe re-run against main, #136, #137 trees):

- **P3 is fixed and pinned.** Selective extract/stream of one early solid-7z
  member: 31.0× over-decode → exactly 1.00×, full sequential read still decodes
  once; regression tests in `test_solid.py` / `test_measurement.py` pin it.
- **H2's attribution is revised — wrapper layering is *not* the ZIP gap.**
  #136+#137 implemented the wrapper-side H2 candidates (readall join, nested
  `ArchiveStream` collapse, verify fusion: STORED stack is now
  `ArchiveStream → SlicingStream`) and ZIP read-all wall did not move (±2%,
  within noise, on both their harness runs and my independent probe). The real
  cost is **decode granularity**: `_COMPRESSED_READ_SIZE = 8192` feeds ~8 KiB
  compressed slices through a 5-frame Python loop ~17×/member while `zipfile`
  decompresses each member in a single C call. Raising the feed (or a
  known-size single-shot fast path) takes ZIP read-all from 1.38× → **1.23×
  stdlib** on this host — under the 1.3× budget. Full numbers, remaining
  per-member overhead (~190 µs/member, distributed), and the investigation
  plan: `residual-gap.md`.
- **Still open at that commit (historical):** P1, P2, P4/P5, P6, Q1–Q4/Q6.
  Superseded by the #139 / #140 / #141 updates below.

## Second follow-up (#139, `main` @ `93dc28e`) — verified

#139 implemented the `residual-gap.md` plan; I verified it independently (full
suite green, gate green, probes re-run, before/after probe on shared fixtures):

- **Decode-feed fix confirmed.** ZIP read-all **1.41× → 1.20×** on the review
  host; OS-level `read()` census 1220 → **196** (zipfile = 195). H1 stays 1.00×.
- **P4 and P5 fixed.** All three `repro.py` adversarial probes now CAUGHT
  (`SOLID_DECODE_FACTOR` 1.25, non-solid over-decode ×1.1, seek slack
  baseline+8); `sevenzip_solid_random` gated vs baseline×1.5 (Q6).
- **The regime split is the story now** (#139 Track 2): 4 KiB members ≈ 4×
  (pure per-member machinery, feed-size-insensitive), 256 KiB ≈ 1.38×,
  1 MiB ≈ 1.30×. Q1 has a maintainer direction as of 2026-07-18 — metadata
  ops budgeted as *ratios vs the relevant peer* (2–3×/member vs
  `zipfile`/`tarfile`; parity vs `py7zr`/`rarfile` for the native parsers) —
  see `QUESTIONS.md` Q1 for the consequences (listing peers in the harness;
  ZIP open+list becomes in-budget work again).
- **Side-finding (security register O8):** while triaging #139's Windows CI
  failure — a pre-existing flake, not the PR — measured that **~0.3% of
  py7zr-written header-encrypted 7z archives open as an *empty* archive under
  a wrong password** (no error; py7zr stores no encoded-header CRC, and the
  garbage occasionally parses as a zero-member header). Hazard + proposed
  deterministic tightening (reject file-less encoded headers) written up in
  `docs/internal/threat-model.md` O8.

## Third follow-up (#141) — O8 mitigated

**Fixed in #141:** empty decoded `kEncodedHeader` → `EncryptionError` (reader +
pipeline). Threat-model O8 marked mitigated; residual is only garbage that
parses as a *non-empty* plausible header.

## Remaining open (triage 2026-07-18 — see `../STATUS.md`)

| # | Status |
|---|--------|
| P1 | open — needs **Q2** (enforcement venue) |
| P2 | **partial** — large-member ZIP read in budget; many-small / open+list improved toward Q1 2–3× (ZIP many-small ~4.4×→~3.7×) but not yet inside band; extract realistic in ~2× band |
| P3 | **fixed** (#136) |
| P4 / P5 | **fixed** (#139) |
| P6 | **partial** — ZIP peers in #139; **py7zr/rarfile + TAR open_list peers added** (this change); RAR/encrypted/accel data cases still missing |
| P7 | **partial** — model-build fast paths landed (ASCII bidi/utf8, normalize, compression tuples, no-link short-circuit, cheaper metadata accounting); still above 2–3× on ZIP open+list |
| P8 / P9 | **follow-up** (future / archive-copy) |
| Q1 | **direction recorded** (#140) — listing peers + ZIP model-build pass implemented; residual band miss remains |
| Q2 / Q4 | **need decision** |
| Q3 / Q5 / Q6 | **resolved** |

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
- `residual-gap.md` — post-#136/#137 attribution of the remaining ZIP gap +
  next investigation areas and methodology.
- `repro.py`, `measurements.py`, `attrib.py` — runnable evidence.
