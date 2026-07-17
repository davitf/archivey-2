# Brief — Performance & the ≤1.3× stdlib budget

Read `review/README.md` (conventions, VISION tie-breakers, deliverable shape). This
is a **non-security** review of VISION claim #4 — the one load-bearing promise never
reviewed on its own terms. It produces **numbers**, not opinions.

## Why now

VISION's performance budget is explicit and measurable:

> Target **≤ 1.3×** stdlib wall-time for the common paths (open/list/read/extract on
> ZIP and TAR); up to ~2× where a safety/correctness feature justifies it. The
> bottleneck is data movement and *re*-decompression, not header parsing — so the
> benchmark suite must track **bytes decompressed and seek patterns**, not just wall
> time. *An implementation that re-reads a solid block fails the benchmark even if a
> small test corpus hides it.*

The benchmark gate landed (#100 structural gate + nightly wall drift, #111 human
report; `benchmarks/`, `measurement.py`, `benchmarks/baselines/structural.json`). So
the question is no longer "is there a gate" (old finding #5, closed) but **"does the
gate actually enforce the budget, and is the budget actually met?"** — which nobody
has checked end to end. `0.2.0` is where "≤1.3×" goes from aspiration to a claim
users will benchmark themselves.

## Scope

- `benchmarks/harness.py`, `fixtures.py`, `RESULTS.md`, `baselines/structural.json`,
  `tar_iso_lock_baseline.py`; the CI wiring for the structural gate + nightly wall job.
- `internal/measurement.py` (`ByteCounter`/`SeekCounter`, `enable_measurement`) and
  the `BaseArchiveReader` counters it feeds.
- The real hot paths: `codecs.py` accelerator/AUTO gate
  (`RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE`), the 7z solid-folder access path
  (`sevenzip_reader`/`sevenzip_pipeline`), per-`open()` overhead, and materialization
  vs streaming costs in `base_reader.py`.
- `access-mode-and-cost` spec + `cost.py` (the cost model *predicts* these costs;
  check prediction vs reality).

## What to measure (ranked by VISION stakes)

### A. Does the gate measure what VISION says it must? (meta-review first)
- The budget is defined on **bytes-decompressed and seek patterns**, not just wall
  time. Does the structural gate actually assert on `ByteCounter`/`SeekCounter`
  deltas, and would it **fail** the canonical trap — reading every member of a solid
  7z folder out of order re-decoding the folder from its start each time (O(n²))? Write
  that adversarial case and confirm the gate catches it. If it doesn't, the gate is
  theatre and that's the headline finding.
- Is the structural baseline (`structural.json`) meaningful (tight enough to catch a
  regression, loose enough not to flake), and is the nightly wall job comparing
  against a real stdlib baseline for the ZIP/TAR common paths the budget names?
- Coverage: which formats/paths have no benchmark at all? (RAR via `unrar` pipe, native
  ZIP AES/codecs #106, the accelerated deflate/zlib path #105 — all post-date the
  original gate.)

### B. Is ≤1.3× actually met on the common paths? (produce the table)
Measure archivey vs the stdlib equivalent (`zipfile`/`tarfile`/`gzip`/`lzma`) for
open / list / read-all / extract-all on representative ZIP and TAR fixtures, and
report the ratios. Where it exceeds 1.3×, attribute it: safety feature (justified,
≤2×), a real inefficiency, or measurement noise. Specifically check:
- **Per-`open()` overhead** — old finding D2: `capture_open_site` cost + retained
  memory on the million-member dedupe sweep. Is open() cheap enough to list-and-hash
  a huge archive, and did the D2 lazy/gated follow-up land?
- **Listing cost** — does `members()` / `get_members_if_available` match its advertised
  `ListingCost`, or does a backend do more work than the cost claims (directory walk,
  finding #4)?
- **The VerifyingStream / digest path** — verification adds work on every read; is it
  within budget, and is it skippable when the caller doesn't need it?

### C. The accelerator AUTO gate (#105) — is the policy honest?
rapidgzip now backs deflate/zlib above `RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE`.
- Is the threshold set where accelerate actually *wins*? Measure both sides of the
  gate on the same input — below it (stdlib) and just above (rapidgzip) — and confirm
  the crossover is real, not a pessimization for mid-size members.
- Thread/startup overhead: rapidgzip spawns C++ workers. On many small-to-mid members
  (a ZIP of thousands of files), does per-member accelerator setup cost more than it
  saves? Is that why the gate exists — and is it tuned right?
- Confirm the just-fixed per-read memory blow-up (archived stream-decoder F3, fixed in
  #128) is actually bounded now: a `read(1)` on a big compressed member must not buffer
  the whole decoded output. Measure peak RSS.

### D. Solid / re-decompression traps (the VISION exemplar)
- 7z solid folder random `open()` — quantify the O(n²) re-decode and confirm the cost
  model flags it (`AccessCost.SOLID`) *and* the benchmark gate would catch a regression
  that made a non-solid path accidentally re-decode.
- Streaming vs materialization: does `stream_members()` avoid re-work that random access
  incurs, and is the difference the cost model's `SEEKABLE`/`SOLID`/`DIRECT` axes
  predict borne out in bytes-decompressed counts?

## Non-goals
- Not a micro-optimization pass. The deliverable is "is the budget met and enforced,"
  with attributed hotspots — not a stream of speedups.
- Don't propose new accelerator backends or a rewrite; propose gate/threshold/cost-model
  fixes and the specific hotspots worth addressing before 0.2.0.
- Correctness of the accelerator path is settled (security round F2); this is speed/memory.

## Deliverable
Per README. Suggested theme files: `gate-efficacy.md` (does the gate enforce the
budget + the O(n²) test — likely the headline), `budget-table.md` (the archivey-vs-stdlib
ratios), `hotspots.md` (attributed, with `ByteCounter`/`SeekCounter` evidence). Every
claim backed by a reproducible measurement (state machine/fixtures/config; ratios not
adjectives). Distinguish "0.2.0 blocker" (budget missed or gate can't catch a regression)
from "tracked follow-up."
