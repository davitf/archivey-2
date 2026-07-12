## Why

Native 7z (and soon RAR) parsers accept hostile headers in pure Python — the
surface VISION bets on for memory-safe parsing. Mutation fuzz and Hypothesis
cover deterministic “never raw exception” paths, but miss crafted-header bugs
that need coverage guidance (review L1 / threat-model O5). The project is
bursty and often dormant, so plain nightly CI wastes runs and buries failures;
fuzz should fire when `main` moves.

## What Changes

- Add shared Atheris harness infrastructure (seed corpus, partitioned budgets,
  crash artifact upload, one-line repro) behind a CI-only `fuzz` dependency
  group (not a runtime extra — packaging forbids test-only packages in
  user-facing extras).
- **CRC/checksum fixup** after mutation for CRC-gated targets (especially 7z
  headers): recompute and patch valid CRCs so coverage guidance reaches
  post-check parser paths; keep a minority of deliberately broken-CRC inputs
  so the reject path stays covered. Do not rely on libFuzzer CMP feedback to
  solve CRC32.
- Main-push workflow (~120s partitioned) + `workflow_dispatch` (longer budgets
  via env). No always-on nightly.
- Targets: 7z header parse (deep); 7z open+members; `detect_format` prefixes;
  ZIP/TAR/(ISO) open+members (shallower); RAR scaffold (skip until registered).
  Accelerators off; extract out of scope for this harness.
- Extend `testing-contract` with the coverage-guided native/entry-point fuzz
  gate; update threat-model O5 status. Mutation/`ARCHIVEY_FUZZ` harnesses stay.
- Out of scope: OSS-Fuzz, accelerator subprocess sandbox, `SECURITY.md`
  (separate follow-up).

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `testing-contract`: coverage-guided Atheris entry gate; target matrix;
  CRC fixup contract; CI trigger/budget; relationship to mutation/Hypothesis.
- `packaging-and-extras`: document the CI-only `fuzz` dependency group
  (atheris); confirm it is absent from `[all]` / `[recommended*]` / runtime
  extras.

## Impact

- **CI:** new workflow (or job) on `push` to `main` + `workflow_dispatch`;
  failure uploads repro inputs; does not enlarge the PR test matrix.
- **Deps:** `atheris` only via `dependency-groups.fuzz` / fuzz job install.
- **Tests:** new harness modules under `tests/`; existing mutation env gates
  unchanged.
- **Public API / runtime:** none.
- **Docs:** threat-model O5; brief CONTRIBUTING/AGENTS note on how to run.
