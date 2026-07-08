# Retire the frozen DEV oracle

## Why

The frozen DEV oracle tree (`tests/_dev_oracle/`) has silently stopped serving its
purpose: pytest never collects it (`norecursedirs = ["_dev_oracle"]`), its test drivers
import v1 APIs that no longer exist, and it contains two divergent copies of the same
suite (top-level and `_dev_oracle/archivey/`). Meanwhile its genuinely durable asset —
the declarative archive corpus (`sample_archives.py`, ~1300 lines of archive shapes with
expected contents) — has barely been ported: v2's `tests/sample_archives.py` is ~100
lines. PLAN.md still describes the oracle as a "regression gate", which no longer matches
reality. The plan already schedules oracle deletion for Phase 10; doing the corpus port
and deletion now removes dead weight, recovers the corpus coverage while the DEV tree is
still conveniently in-repo, and makes the test-strategy documentation truthful again.

## What Changes

- Port the archive shapes present in the DEV declarative corpus but missing from v2's
  `tests/sample_archives.py` (for formats implemented so far: ZIP, TAR + compressed
  variants, single-file compressors, ISO, directory), including their expected-contents
  metadata. 7z/RAR shapes are ported as corpus *entries* but marked for Phase 7
  activation (their readers do not exist yet).
- Add a corpus-driven **conformance sweep** to the new suite: every corpus archive for an
  implemented format must open, list members matching the declared expected contents, and
  extract cleanly — or raise its documented error (encrypted without password, unsupported
  variant, adversarial member). One parametrized driver, not per-archive test functions.
- **Delete `tests/_dev_oracle/`** (both copies) and the `norecursedirs` /
  tool-exclusion configuration entries that reference it.
- Update `PLAN.md`'s test-strategy section (the "frozen oracle" narrative and the Phase 10
  deletion task) and `CONTRIBUTING.md`'s mention of the oracle exclusions to match.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `testing-contract`: add the corpus conformance-sweep requirement (every declarative
  corpus archive for an implemented format opens/lists/extracts or raises its documented
  error) and record that the frozen DEV oracle tree is retired — the declarative corpus
  plus the oracle *libraries* (py7zr/rarfile/CLIs, which stay for Phase 7
  cross-validation) are the surviving assets.

## Impact

- `tests/_dev_oracle/` (deleted), `tests/sample_archives.py` (grows), one new
  corpus-sweep test module, `tests/conftest.py` (generation/cache hooks if gaps found).
- `pyproject.toml` (`norecursedirs`, ruff/type-checker excludes), `PLAN.md`,
  `CONTRIBUTING.md`.
- No library (`src/`) changes expected; if the sweep surfaces backend bugs, those are
  fixed as their own changes.
