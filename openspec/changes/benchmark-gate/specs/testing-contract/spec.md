## ADDED Requirements

### Requirement: Performance budget is measured and gated

The system SHALL provide a benchmark harness that measures, per format and per
operation (open, list, read-all, extract), three axes: wall time, total bytes
decompressed, and source seek count. Bytes-decompressed and seek-count SHALL be read
from archivey's own stream instrumentation, not estimated from wall time. Bytes
decompressed counts decode/output volume (distinct from the existing compressed-input
`compressed_bytes_consumed` live-ratio counter; both MAY be available together). The
harness SHALL run as a CI gate over a fixed comparison corpus and fail when a tracked
metric regresses past its recorded baseline.

Wall-time SHALL be gated as a ratio against the stdlib peer for that format
(ZIP→`zipfile`, TAR→`tarfile`, single-file gzip→`gzip`), honoring the `VISION.md`
budget (≤1.3× common paths; up to ~2× where a safety/correctness feature justifies it,
annotated per case). Bytes-decompressed and seek-count SHALL be gated as deterministic
structural invariants (exact value or ≤ bound), since they are host-independent.
Structural invariants SHALL gate (block) every PR. Full wall-time ratio checks SHALL run
on every PR/push as well, but as a **non-blocking** job (`continue-on-error`): shared-runner
ratios are too noisy to block merges, so the job records results (JSON artifact +
informational VISION print) and fails visibly only on a gross regression past the sanity
ceiling. A nightly `schedule` SHALL NOT be used — this project is bursty and often dormant,
so per-PR execution catches regressions immediately whereas nightly would mostly run on
unchanged code or skip during dormancy.

The harness SHALL enforce the solid-block no-re-decode invariant: reading every member
of a solid archive (7z folder / solid RAR) in listing order SHALL decompress each packed
byte at most once (total bytes-decompressed ≤ unpacked size × a small constant). Random
out-of-order `open()` MAY re-decode (the documented `AccessCost.SOLID` cost) and is
recorded but not failed. Baselines SHALL be committed, reviewable artifacts; a metric
change requires an explicit baseline diff.

#### Scenario: benchmark axes and gating

| Case | Expected |
| --- | --- |
| ZIP/TAR/gzip open·list·read·extract | Wall-time ratio vs stdlib peer within the `VISION.md` budget or the annotated exception |
| Sequential read of every member of a solid 7z folder | Bytes-decompressed ≤ folder unpacked size × constant (no per-member re-decode) |
| Out-of-order random `open()` on a solid folder | Re-decode recorded, not failed (documented SOLID cost) |
| A change that re-reads a solid block from start per member | CI gate fails on the bytes-decompressed invariant |
| Metric drifts past recorded baseline | Gate fails until the baseline diff is reviewed and updated |
| Benchmark run on a noisy CI host | Ratio tolerance band absorbs host variance; structural invariants stay exact |
