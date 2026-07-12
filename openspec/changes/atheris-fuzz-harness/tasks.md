## 1. Packaging and docs

- [ ] 1.1 Add PEP 735 `fuzz` dependency group with `atheris`; ensure absent from runtime extras / `[all]`.
- [ ] 1.2 Update threat-model O5 (Atheris gate status; Hypothesis landed; SECURITY.md/OSS-Fuzz still later).
- [ ] 1.3 Short CONTRIBUTING/AGENTS note: how to run fuzz locally and what the main-push job does.

## 2. Shared harness

- [ ] 2.1 Implement shared Atheris runner: seed corpus from declarative/adversarial fixtures, accelerators off, per-slice timeouts, typed-error success contract.
- [ ] 2.2 On failure: persist crashing input, upload CI artifact, print one-line repro command.
- [ ] 2.3 Env overrides for per-target budgets (main defaults ≈ partitioned 120s).

## 3. Targets

- [ ] 3.1 7z header-parse target (largest budget slice).
- [ ] 3.2 7z `open_archive` + members/materialize target.
- [ ] 3.3 `detect_format` prefix target.
- [ ] 3.4 ZIP + TAR open+members shallow targets.
- [ ] 3.5 ISO open+members target with hard wall-clock kill.
- [ ] 3.6 RAR scaffold target that skips until backend registered.

## 4. CI workflow

- [ ] 4.1 Add workflow: `push` to `main` + `workflow_dispatch`; Linux only; `uv sync --group fuzz` (+ needed extras for ISO/etc.).
- [ ] 4.2 Do not attach Atheris to the default PR test matrix.
- [ ] 4.3 Confirm mutation / `ARCHIVEY_FUZZ` harnesses still run as today.

## 5. Verify

- [ ] 5.1 Local smoke: each target runs briefly under Atheris without raw exceptions on seed corpus.
- [ ] 5.2 `openspec validate --strict atheris-fuzz-harness`
- [ ] 5.3 Packaging audit / extras guard still green with `fuzz` group present.
