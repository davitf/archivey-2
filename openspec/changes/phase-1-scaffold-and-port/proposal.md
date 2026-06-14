# Phase 1: Project scaffold and initial port

## Why

The v2 selective rewrite (see `PLAN.md`) starts from the existing `archivey-dev`
codebase. Before changing any interface, we need a new `archivey` project that
**compiles, is mypy-clean, and passes the ported DEV test suite** — a known-good
green baseline. Every later phase (stream-layer reorg, reader-interface cleanup,
extraction rewrite, public-API alignment) is a refactor on top of this baseline,
so establishing it first is what makes those phases safely incremental.

## What Changes

- **New `pyproject.toml`** — `hatchling` build backend; PEP 621 metadata
  (`archivey`, `0.2.0.dev0`, `requires-python >=3.11`); runtime extras under
  `[project.optional-dependencies]`; dev tooling under `[dependency-groups]`.
- **uv-based workflow** — adopt `uv` for environment management, locking
  (`uv.lock`), and running tools, while keeping the package fully
  pip-installable (standard metadata, no uv lock-in).
- **Port `src/archivey/` from DEV verbatim** — gives a compiling, passing
  baseline before any interface edits.
- **Port test infrastructure** — `sample_archives.py`, `create_archives.py`,
  `conftest.py`, `testing_utils.py`, and all `test_*.py`; move committed binary
  archives to `tests/fixtures/`.
- **Tooling config** — `ruff`, `mypy --strict`, `coverage`.
- **No public API changes** — this is a private fork baseline.

## Specs

This change **implements** already-written specs; it does not modify them, so it
carries no spec deltas. The capabilities it realizes or touches:

- **`packaging-and-extras`** — realized directly (pyproject, extras→format
  mapping, env matrix, `__version__`).
- **`format-*`, `format-detection`, `archive-reading`, `archive-writing`,
  `backend-registry`, `error-handling`, `logging`** — code ported verbatim from
  DEV; behavior should match DEV, not yet the cleaned-up target interfaces (those
  arrive in Phases 2–5).

## Impact

- **Affected code:** new repository scaffold; `src/archivey/` ported; `tests/`
  ported; `tests/fixtures/` for committed binaries.
- **Tooling:** uv for local + CI workflows (`astral-sh/setup-uv`); full CI matrix
  is deferred to Phase 10 per `PLAN.md`.
- **Risk:** low. This is a verbatim port; acceptance is simply "the DEV tests
  pass under the new project." The main watch-item is dropping or quarantining
  tests that depend on DEV-only API that this repo will not carry forward.
