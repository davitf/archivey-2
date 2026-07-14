## 1. Stream instrumentation

- [ ] 1.1 Expose total bytes-decompressed for a read pass (reuse the `compressed-streams` compressed-bytes counter; aggregate per archive/operation)
- [ ] 1.2 Add a source-seek counter on the source-facing stream wrapper if not already exposed; ensure zero overhead when measurement is off

## 2. Harness

- [ ] 2.1 `benchmarks/harness.py`: run (format, operation) cases, record wall time + bytes-decompressed + seek count; emit JSON
- [ ] 2.2 stdlib baselines: `zipfile`/`tarfile`/`gzip` peers for the common-path wall-time ratios
- [ ] 2.3 Large solid 7z + solid RAR fixtures generated on demand (not committed) so the O(n²) signal is visible
- [ ] 2.4 `benchmarks/baselines/*.json` recorded baselines; comparison with a tolerance band for wall time and exact/≤ bounds for structural metrics

## 3. Invariants and gate

- [ ] 3.1 Solid-folder sequential-read invariant: bytes-decompressed ≤ unpacked size × constant; a from-start-per-member re-decode fails it
- [ ] 3.2 Out-of-order random `open()` records re-decode without failing
- [ ] 3.3 CI job runs the harness on the fixed corpus and fails on a past-budget regression (decide PR vs nightly split per design open question)

## 4. Verify

- [ ] 4.1 Introduce a deliberate solid re-decode regression locally and confirm the gate fails; revert
- [ ] 4.2 `openspec validate --strict benchmark-gate`
