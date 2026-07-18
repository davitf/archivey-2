# Performance investigation — post-#136/#137 tracks

**Branch:** follow-up to PR #134’s residual analysis (`residual-gap.md` / `hotspots.md`).
**Measured at:** `main` @ `b9cdeac` + this change set.
**Host:** Cursor Cloud agent VM (x86_64, CPython 3.11). Wall numbers are
directional; every A/B below is single-process, warmed, median of ≥9 rounds
unless noted. Repro: `attrib.py`, `repro.py`, `measurements.py`, and the harness.

## Executive conclusions

1. **Decode granularity was real and is fixed.** Raising the compressed feed from
   8 KiB → 64 KiB (with scale-up to the bounded `read(n)` request, capped at 1 MiB)
   collapses ZIP member inflate to ~one OS read / one C call. On the review probe
   fixture (64 × 256 KiB): **OS `read()` calls 1220 → 196** (zipfile = 195);
   wall **~1.56× → ~1.37×** zipfile. `#128` / F3a `read(1)` buffer + RSS bounds hold.
2. **Harness “2×” vs probe “1.4×” is a regime split, not a contradiction.** CI
   fixtures are **8 × 4 KiB** (many-small); the attributed probe is **64 × 256 KiB**.
   Member-size sweep at fixed 16 MiB total:

   | member size | n | ratio (pre → post feed) |
   |---:|---:|---:|
   | 4 KiB | 4096 | **4.05×** (feed: no effect) |
   | 64 KiB | 256 | 1.74× → **1.59×** |
   | 256 KiB | 64 | 1.40× → **1.38×** |
   | 1 MiB | 16 | 1.30× → **1.30×** |

   Realistic harness ZIP read-all lands ~**1.7–1.8×** (under the ~2× safety band;
   still above 1.3×). CI ZIP read-all stays ~**4×** because it is pure per-member
   machinery.
3. **Per-member fixed cost is the next real budget fight — and has no cheap win.**
   Profile of 2000 × 4 KiB: zlib decompress is ~1% of tottime; top costs are
   `_to_member`, `dataclasses.replace`, `ArchiveStream` construction (≈3/member),
   `_local_data_region`, `_wrap_member_stream`. Ablations did not yield a ≥5%
   move on the many-small fixture without invasive API/shape changes. Left open.
4. **Extract residual is a deliberate safety floor, not a missed micro-opt.**
   Fresh 64 × 64 KiB ZIP extract: archivey **~3.7×** zipfile; `strace -c` shows
   ~523 `lstat` / 64 `rename` / 64 `unlinkat` (mkstemp+replace) vs zipfile’s
   ~7 `lstat` / 0 `rename`. Realistic harness extract ~**1.9×** (inside ~2×).
   No code change this pass — product call is Q1 (is ~2× the claim?).
5. **Gate holes G3/G4/G5 are closed on the PR structural path.**

   | Probe | Before | After |
   |---|---|---|
   | solid-collapse O(n²) | CAUGHT | CAUGHT |
   | solid-double exactly 2× | NOT CAUGHT | **CAUGHT** (`SOLID_DECODE_FACTOR` 2.0 → 1.25) |
   | zip-double decode-twice | NOT CAUGHT | **CAUGHT** (nonsolid over-decode ×1.1 + seek slack baseline+8) |
   | solid-random “got worse” | ungated | **gated** vs baseline×1.5 (ci scale only) |

6. **Harness now reports ZIP open_list / extract vs stdlib peers** (G7 partial),
   so the 4–6× CI-scale metadata/extract ratios are visible in the table instead
   of silent `ratio=None`.

## What shipped

### Library

- `decompressor_stream.py`: `_COMPRESSED_READ_SIZE = 65536`;
  `_compressed_feed_size(max_length)` scales large bounded reads up to 1 MiB so
  fused-verify whole-member `read(declared_size)` is single-shot. Comment
  documents why 8 KiB was wrong for ZIP parity.

### Benchmark gate

- `SOLID_DECODE_FACTOR = 1.25` (addresses Q3 / G3).
- `NONSOLID_DECODE_FACTOR = 1.1` over-decode on zip/tar/gzip `read_all` (G4).
- `SEEK_BASELINE_SLACK = 8` replaces `baseline×2+8` (still absorbs gzip 1→3 jitter;
  catches zip-double 28→52).
- `SOLID_RANDOM_BYTES_FACTOR = 1.5` on ci-scale only (G5) — must not use the ci
  baseline against realistic corpora (caught during this investigation).
- ZIP `open_list` / `extract` stdlib wall peers wired into `run_cases`.
- `structural.json` metadata updated to match the new factors.

### Review artifacts

- This report; `repro.py` probe text updated; PR #134 theme files retained for
  provenance (their “open” status for P3/H2 candidates is historical — see
  statuses below).

## Track-by-track detail

### Track 1 — Decode granularity (implemented)

**Hypothesis (from `residual-gap.md`):** 8 KiB compressed feeds force ~17 Python
loop trips per 256 KiB member; zipfile does one C inflate.

**Evidence:**

- Feed sweep (mirrored order, in-process): plateau at ≥64 KiB.
- Census after change: archivey OS reads ≈ zipfile.
- `test_decompressor_read_one_bounds_internal_buffer` still green;
  RSS delta on `read(1)` of a 32 MiB highly-compressible gzip ≈ 0.

**Non-goals preserved:** verification, translation, leases; output still bounded
by `max_length`.

### Track 2 — Harness vs probe decomposition (investigation only)

Fitted qualitatively as:

```
ratio ≈ (per_member_overhead × n + per_byte_overhead × bytes) / stdlib
```

At 4 KiB, per-member term dominates (feed size irrelevant). At ≥256 KiB, per-byte
/ decode-loop term dominated before this change and is now near the residual
~190 µs/member floor from `residual-gap.md`.

**Implication for Q1:** claiming ≤1.3× on “ZIP read” without naming the member-size
regime is misleading. CI harness will keep printing ~4× until many-small cost
drops; realistic corpora are the fair VISION comparison and are now ~1.7× read /
~1.9× extract on this host.

### Track 3 — Per-member fixed cost (not shipped)

cProfile (2000 × 4 KiB, 3 rounds) top tottime: `_to_member`, `replace`,
`ArchiveStream.__init__` (3×/member), `_local_data_region`, wrap/verify setup.
zlib.decompress ≈ 1% of tottime.

Candidates considered and deferred:

| Idea | Why deferred |
|---|---|
| Cache `_local_data_region` by `ZipInfo` id | Fragile under shared `ZipInfo`; CRC failure in naive ablate |
| Fold remaining `ArchiveStream` constructions | #136 already collapses nested wraps; remaining wraps are load-bearing (lazy + verify + codec) |
| Lazy `_to_member` field work | Touches archive-data-model / listing contract; needs its own change |

**Accept criterion from residual-gap (≥5% on 1000×4 KiB) was not met** by any
safe one-liner. Recommend a dedicated change if Q1 treats many-small as in-budget.

### Track 4 — Extract-all residual (investigation only)

`strace -c` on 64 × 64 KiB DEFLATE ZIP, fresh dest:

| syscall | archivey | zipfile |
|---|---:|---:|
| `lstat` | 523 | 7 |
| `openat` | 348 | 148 |
| `rename` | 64 | 0 |
| `write` | 64 | 128 |
| `unlinkat` | 65 | 65 |
| wall | 33.8 ms | 9.0 ms (**3.74×**) |

Atomic `mkstemp`+`os.replace` and overwrite/lexists policy explain the rename and
most of the lstat tax. Parent-resolve reuse would only help symlink-heavy trees;
the default FILE path’s cost is the safety model itself. Realistic harness
extract ~1.9× already sits in VISION’s ~2× “safety justifies it” band.

### Track 5 — Gate efficacy (implemented)

`repro.py` after this change:

```
unpatched gate: green
solid-collapse: CAUGHT
solid-double (exactly 2×): CAUGHT
zip-double: CAUGHT  (bytes over-decode + seek slack)
```

Still **not** enforced: absolute wall ≤1.3× / ≤2× on PR (Q2). Nightly remains
informational for VISION; sanity ceiling stays 10×. Recommendation from #134
(Q2 a+c) still stands — this PR did (c)-lite by adding missing ZIP peers.

## Open questions (updated)

| # | Status after this PR / later |
|---|---|
| Q1 budget scope | **Direction recorded** (#140): listing = peer ratios (2–3× ZIP/TAR; parity 7z/RAR). Implementation open — see `../STATUS.md` |
| Q2 wall enforcement | Still open; peers added for ZIP open/extract |
| Q3 `SOLID_DECODE_FACTOR` | **Done here** (1.25) |
| Q4 verify-skip knob | Unchanged; perf case still ~nil post-#137 — lean leave-as-is |
| Q5 H1 fix shape | Resolved in #136 |
| Q6 solid-random bound | **Done here** (ci ×1.5) |
| O8 (side-finding) | **Mitigated in #141** |

## How to re-verify

```bash
uv run --no-sync python review/performance/repro.py
uv run --no-sync python review/performance/attrib.py bench
uv run --no-sync python review/performance/attrib.py census
uv run --no-sync python -m benchmarks.harness --mode structural --scale ci
uv run --no-sync python -m benchmarks.harness --mode full --scale realistic --json-out /tmp/bench.json
uv run --no-sync pytest tests/test_codecs.py::test_decompressor_read_one_bounds_internal_buffer -q
```
