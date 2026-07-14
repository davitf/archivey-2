## Context

`VISION.md` (Performance budget) sets ≤1.3× stdlib wall-time on ZIP/TAR common paths,
~2× where safety justifies it, and — crucially — says wall time alone hides the real
cost: re-decompression and seek storms only show up on large corpora. The cost model
(`access-mode-and-cost`) already *names* the trap (`AccessCost.SOLID`), and
`sevenzip_reader.py` random `open()` re-decodes a solid folder from its start per member.
Nothing gates it. `compressed-streams` already "counts compressed bytes consumed"
(spec §270), so the decompression-volume signal is half-built.

## Goals / Non-Goals

**Goals:**
- A benchmark harness that reports wall time, bytes-decompressed, and source-seek count
  per (format, operation).
- Host-tolerant CI gating via *ratios against recorded baselines*, not absolute numbers.
- A concrete anti-regression invariant for the solid-block O(n²) trap.

**Non-Goals:**
- Micro-optimizing any current path (this change only measures + gates).
- Benchmarking against py7zr/rarfile/libarchive (nice-to-have; a later add — they are
  dev-only oracles, not budget peers).
- A public performance API surface.

## Key decisions

- **Three axes, two enforcement modes.** Wall-time is gated as a ratio vs the stdlib
  peer (ZIP→`zipfile`, TAR→`tarfile`, gzip→`gzip`); bytes-decompressed and seek-count are
  gated as *structural invariants* (exact or ≤ bound) because they are deterministic and
  host-independent. The structural invariants are the ones that actually catch the solid
  trap; wall-time ratio is the coarse backstop.
- **Solid-folder invariant.** Reading every member of a solid 7z folder *in listing
  order* SHALL decode each packed byte at most once (total bytes-decompressed ≤ folder
  unpacked size × small constant). Random out-of-order `open()` is allowed to re-decode
  (that is the documented SOLID cost) — the benchmark records it but does not fail it;
  the invariant is about the sequential sweep the founding use case performs.
- **Instrumentation source.** Reuse the `compressed-streams` compressed-bytes counter for
  bytes-decompressed; add a lightweight seek counter on the source-facing stream wrapper
  if one is not already exposed. Measurement must not perturb the hot path when disabled.
- **Baselines are recorded, reviewed artifacts.** A committed
  `benchmarks/baselines/structural.json` keyed by case holds seek/byte reference
  counts; the PR gate compares seek counts against them with slack. There is
  **no** committed `wall_time.json` — cold-pass ci ratios were misleading, and
  shared-runner noise makes wall-ratio regression gates flake.
- **Corpus.** Reuse the declarative test corpus plus a small set of deliberately-large
  solid archives generated on demand (not committed), so the O(n²) signal is visible.

## Open questions (resolved during apply)

- **Wall-time tolerance:** sanity ceiling `WALL_RATIO_BUDGET=10` only in `--mode full`.
  No committed wall-ratio baseline. VISION ≤1.3× / ~2× is informational on realistic
  full runs (printed, not hard-fail).
- **PR vs nightly:** structural invariants (seek ≤ bounds + solid decode-once) on every
  PR via the `benchmark` CI job + `tests/test_benchmark_gate.py`. Decode-once is also a
  first-class unit test (`test_measurement.py`, 7z + committed solid RAR, ×1.1 bound).
  Full wall-time mode runs non-blocking on `schedule` / `workflow_dispatch`
  (`benchmark-wall` job, JSON artifact).
- **Peak memory / tracemalloc:** deferred — fourth axis later (cross-ref threat-model O1).

## Instrumentation note (apply clarification)

`compressed_bytes_consumed` remains the compressed-*input* live-ratio counter.
`bytes_decompressed` is a separate opt-in output counter (folder/member decode layer).
Both coexist; measurement is off by default (`enable_measurement()` context).
