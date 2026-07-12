## 1. Packaging and docs

- [x] 1.1 Add PEP 735 `fuzz` dependency group with `atheris`; ensure absent from runtime extras / `[all]`.
- [x] 1.2 Update threat-model O5 (Atheris gate status; Hypothesis landed; SECURITY.md/OSS-Fuzz still later).
- [x] 1.3 Short CONTRIBUTING/AGENTS note: how to run fuzz locally and what the main-push job does.

## 2. Shared harness

- [x] 2.1 Implement shared Atheris runner: seed corpus from declarative/adversarial fixtures, accelerators off, per-slice timeouts, typed-error success contract.
- [x] 2.2 On failure: persist crashing input, upload CI artifact, print one-line repro command.
- [x] 2.3 Env overrides for per-target budgets (main defaults ≈ partitioned 120s).
- [x] 2.4 CRC/checksum fixup helpers: mutate-then-patch valid CRCs for gated layouts; configurable minority broken-CRC inputs; unit-test fixup against known-good headers.

## 3. Targets

- [x] 3.1 7z header-parse target (largest budget slice) **with next_header CRC fixup** by default.
- [x] 3.2 7z `open_archive` + members/materialize target (fixup where the path still CRC-gates listing).
- [x] 3.3 `detect_format` prefix target.
- [x] 3.4 ZIP + TAR open+members shallow targets; apply ZIP CRC fixup only where needed to reach wrapper logic behind checks.
- [x] 3.5 ISO open+members target with hard wall-clock kill.
- [x] 3.6 RAR scaffold target that skips until backend registered (plan CRC/header fixup when enabling).

## 4. CI workflow

- [x] 4.1 Add workflow: `push` to `main` + `workflow_dispatch`; Linux only; `uv sync --group fuzz` (+ needed extras for ISO/etc.).
- [x] 4.2 Do not attach Atheris to the default PR test matrix.
- [x] 4.3 Confirm mutation / `ARCHIVEY_FUZZ` harnesses still run as today.

## 5. Verify

- [x] 5.1 Local smoke: each target runs briefly under Atheris without raw exceptions on seed corpus; 7z header target with fixup enters post-CRC parse on fixed-up seeds; broken-CRC minority still rejects.
- [x] 5.2 `openspec validate --strict atheris-fuzz-harness`
- [x] 5.3 Packaging audit / extras guard still green with `fuzz` group present.
