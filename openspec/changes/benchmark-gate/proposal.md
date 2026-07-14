## Why

`VISION.md` commits to a performance budget (≤1.3× stdlib wall-time on common
paths, ~2× where a safety feature justifies it) and states the real bottleneck is
**re-decompression and seek storms**, not header parsing — so the benchmark must
track *bytes decompressed and seek counts*, "an implementation that re-reads a solid
block fails the benchmark even if a small test corpus hides it." Today none of this
is enforced: `benchmarks/` holds one ad-hoc lock-baseline script, nothing runs in CI,
and the documented 7z solid-block trap (random `open()` re-decodes the folder from its
start per member — O(n²) over a solid folder) is caught by no gate. This is the
review's finding #5 and the highest-leverage release-blocker: it turns VISION's
central perf promise from prose into a regression gate, and it must land **before the
CLI**, whose `test`/`extract` on real solid archives is exactly where that trap bites.

## What Changes

- Add a `benchmarks/` harness that measures, per operation (open / list / read-all /
  extract) and per format, three axes: **wall time**, **bytes decompressed**, and
  **source seek count** — the latter two instrumented from archivey's own stream layer
  (`compressed-streams` already counts compressed bytes consumed) rather than wall time
  alone.
- Assert **budget ratios vs the stdlib baseline** (`zipfile`/`tarfile`/`gzip`) for the
  common paths, and assert **bytes-decompressed / seek invariants** where no stdlib peer
  exists (notably: reading every member of a solid 7z folder in order decodes each
  packed byte at most once — the anti-O(n²) guard).
- Wire the harness as a **CI gate** on a fixed comparison corpus, gating like the type
  checkers: a regression past the budget fails the job. Ratios are asserted against
  recorded baselines, not absolute times, so the gate is host-tolerant.

## Capabilities

### New Capabilities

<!-- none — benchmarking is scaffolding, expressed as testing-contract requirements -->

### Modified Capabilities

- `testing-contract`: add a performance-budget requirement — the benchmark harness,
  the three tracked axes, the solid-block no-re-decode invariant, and the CI gate. This
  becomes the enforcement mechanism for the `access-mode-and-cost` `AccessCost.SOLID`
  re-decode cost (which the cost model documents but nothing currently gates).

## Impact

- New `benchmarks/` suite + a recorded-baseline file; new CI job (gate, host-tolerant
  ratios).
- Stream instrumentation: expose bytes-decompressed and seek counts for measurement
  (reuse `compressed-streams` counters; add a source-seek counter if absent).
- No public-API change; no runtime dependency (stdlib `time`/`tracemalloc` only).
- Unblocks: any public performance claim, and the CLI phase.
