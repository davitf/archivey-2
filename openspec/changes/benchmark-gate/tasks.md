## 1. Stream instrumentation

- [x] 1.1 Expose total bytes-decompressed for a read pass (reuse the `compressed-streams` compressed-bytes counter; aggregate per archive/operation)
- [x] 1.2 Add a source-seek counter on the source-facing stream wrapper if not already exposed; ensure zero overhead when measurement is off

## 2. Harness

- [x] 2.1 `benchmarks/harness.py`: run (format, operation) cases, record wall time + bytes-decompressed + seek count; emit JSON
- [x] 2.2 stdlib baselines: `zipfile`/`tarfile`/`gzip` peers for the common-path wall-time ratios
- [x] 2.3 Large solid 7z + solid RAR fixtures generated on demand (not committed) so the O(n²) signal is visible
- [x] 2.4 `benchmarks/baselines/*.json` recorded baselines; comparison with a tolerance band for wall time and exact/≤ bounds for structural metrics

## 3. Invariants and gate

- [x] 3.1 Solid-folder sequential-read invariant: bytes-decompressed ≤ unpacked size × constant; a from-start-per-member re-decode fails it
- [x] 3.2 Out-of-order random `open()` records re-decode without failing
- [x] 3.3 CI job runs the harness on the fixed corpus and fails on a past-budget regression (decide PR vs nightly split per design open question)

## 4. Verify

- [x] 4.1 Introduce a deliberate solid re-decode regression locally and confirm the gate fails; revert
- [x] 4.2 `openspec validate --strict benchmark-gate`
