# Contributing to Archivey (v2)

Thanks for working on Archivey! This file is the **coding and testing standards**;
the *design* lives elsewhere and is authoritative:

- `openspec/specs/<capability>/spec.md` — the authoritative capability specs.
- `docs/` — end-user guide; `docs/decisions/` — design decision log; `docs/internal/` —
  threat model / codec analysis; `docs/grab-bag/` — historical SPEC/ARCHITECTURE/
  COMPARISON/ASYNC and explorations (not normative).
- `VISION.md`, `PLAN.md`, `IDEAS.md` — vision, roadmap, backlog.
- `openspec/changes/<change>/` — in-flight proposals (propose changes here, don't
  edit shipped specs ad hoc). Default schema is `library` (compact library-style
  deltas); see `openspec/schemas/library/README.md` and `openspec/config.yaml`.
- `CLAUDE.md` — orientation for AI agents working in this repo.

## Getting started

Python **3.11+**. Tooling runs through [`uv`](https://docs.astral.sh/uv/):

```bash
uv sync                       # create/refresh the dev environment
./scripts/install-git-hooks.sh  # required: auto ruff fix+format on commit
uv run pytest                 # run the test suite
uv run ruff check             # lint
uv run ruff format            # format (apply; CI uses `ruff format --check`)
uv run pre-commit run --all-files  # optional: run the framework hooks on the whole tree
uv run pyrefly check          # type-check (Pyrefly)
uv run ty check               # type-check (ty)
```

Pyrefly and ty are scoped to `src/`, so every command above runs clean with no extra
path arguments.

**Format before you commit.** CI runs `ruff format --check` (and `ruff check`) over
`src/ tests/ scripts/ benchmarks/` and will fail on drift. Installing the git hook
(`./scripts/install-git-hooks.sh`) makes this automatic: staged `*.py` under those
paths are `ruff check --fix`'d and `ruff format`'d on commit. If you skip the hook,
run `uv run ruff format` yourself before committing — `ruff format --check` only
reports problems; it does not rewrite files.

(`uv run pre-commit install` remains an alternative if you prefer the
`pre-commit` framework's own installer, but on Cursor Cloud it can land in the
remapped `core.hooksPath`; `./scripts/install-git-hooks.sh` is the supported path.)

RAR *data* tests need the system `unrar` binary (`apt-get install -y unrar`, etc.);
without it those tests skip cleanly.

**Before pushing, run the suite in all three dependency configurations CI runs** —
optional libraries change behaviour by their presence *and* their version, so a change
that passes one way can break another (a codec that's absent, a floor-version library bug,
an accelerator that's only installed at current versions). Run:

```bash
# 1. Current versions, all extras — the everyday leg.
uv sync --group dev --extra all && uv run --no-sync pytest

# 2. Minimum supported versions — every declared dependency pinned to its floor
#    (`pycdlib 1.16`, `zstandard 0.23`, …), so version-specific library bugs in the
#    supported range surface. --no-sync keeps the lowest resolution for the test run.
uv sync --group dev --extra all --resolution lowest-direct && uv run --no-sync pytest

# 3. Zero-dependency core — no extras, no dev group; proves tests needing an optional
#    library skip/xfail cleanly (see the `requires` helper in tests/conftest.py) and the
#    core imports nothing third-party.
uv sync --no-dev && uv run --no-sync python tests/check_zero_dep_core.py \
  && uv run --no-sync --with pytest --with pytest-timeout --with pytest-cov pytest tests/ -q
```

These mirror CI's `[all]`, `[all-lowest]`, and `[core-only]` legs; all three must stay
green. (After the core-only leg, `uv sync --group dev --extra all` to restore your
everyday environment.)

CI also matrixes supported **Python versions** (3.11–3.14 on Linux; 3.11/3.14 on
macOS/Windows). Repo `.python-version` pins the default local env to 3.11, so the
workflow must pass `--python <matrix>` (and `UV_PYTHON`) on every `uv sync` /
`uv run` in the test job — otherwise newer-version legs silently re-test 3.11.

## Tooling decisions

- **Type-checking is Pyrefly + ty** — the library is kept clean on **both**. We do
  **not** use mypy or pyright. What gives *users* correct checks and IDE autocompletion
  is the typed public API plus the `py.typed` marker (PEP 561), independent of which
  checker CI runs; keeping two modern checkers green guards us against either one's
  blind spots.
- **Coverage is reported, never gated.** `pytest-cov` produces a report you can eyeball;
  there is no `fail_under` threshold. Aim for meaningful coverage through the tests
  below, not a number.
- **Zero-dependency core.** The core (incl. native 7z read + RAR metadata) imports no
  third-party packages. Everything else is an optional extra (see
  `openspec/specs/packaging-and-extras/spec.md`). Don't add a runtime dependency to the
  core.

## Coding standards

- **Keep it simple and well typed.** Prefer straightforward code over cleverness; type
  everything that's part of, or feeds, the public API.
- **Don't accumulate debt — clean as you go.** When you touch something, leave it in the
  shape it *should* have, not a quick patch bolted onto the old shape. If a change calls
  for a rename, a moved file, an updated doc/spec, or a small refactor to keep the design
  coherent, do it now as part of the change rather than deferring it — a deferred cleanup
  is debt the next person (often the next phase) inherits. Code and docs/specs are kept in
  sync: renaming a type or changing a contract means updating the prose docs and the
  `openspec/specs/` that describe it in the same change. The one exception is the
  pause-and-ask rule below: when a cleanup would resolve a genuine design discrepancy,
  surface it instead of silently picking a direction.
- **Comments explain *why*, not *what*.** Match the comment density and style of the
  surrounding code. Don't narrate what the code obviously does; do explain non-obvious
  decisions, format quirks, and edge cases (these archives are full of them).
- **Match the surrounding code.** Naming, structure, and idiom should read like the file
  you're editing.
- **Type-checker suppressions must be justified, and are a last resort.** A bare
  `# type: ignore` that hides a *fixable* error is not allowed — it lets a real bug
  through and silently rots. Before suppressing, fix the type model (e.g. declaring the
  named `ArchiveFormat` instances as `ClassVar`s removed ~20 `# type: ignore`s *and* the
  errors they were masking). When a suppression is genuinely unavoidable (a checker bug,
  or a third-party stub gap), it MUST:
  - be **specific** — pin the rule, e.g. `# type: ignore[attr-defined]` /
    `# pyrefly: ignore[...]` / `# ty: ignore[...]`, never a blanket `# type: ignore`; and
  - carry an **inline reason** on the same line or just above, saying *why* it's needed
    and ideally linking the upstream issue.

  An unjustified or non-specific suppression should be treated as a review blocker. The
  library is kept clean on **both** Pyrefly and ty precisely so neither checker's blind
  spot can hide an error the other would catch — don't defeat that with a suppression.
- **Exception translation is specific.** All errors caused by archive problems must
  surface as `ArchiveyError` subclasses, via each reader's per-library translator:
  - Map *known* third-party exceptions to the right `ArchiveyError`
    (`CorruptionError`, `TruncatedError`, `EncryptionError`, …).
  - **Never** add a catch-all that converts *any* `Exception` — that hides bugs. If an
    exception is unrecognized, let it propagate (return `None` from the translator) so we
    learn about it and can map it deliberately.
  - Genuine `OSError` / `KeyboardInterrupt` / `MemoryError` propagate unchanged, except
    where a spec says otherwise (e.g. safe-extraction catches a per-member filesystem
    `OSError` under `OnError.CONTINUE` — see `openspec/specs/safe-extraction/spec.md`).

## Testing standards

- **Test behaviour, not internal implementation.** Assert on what a public API returns
  and does, so refactors don't break tests gratuitously. *Narrow exception:* the
  low-level building blocks — stream primitives/helpers, format parsers, the codec
  layer — should also get focused **unit** tests of their internals, because they're
  shared foundations and their corner cases are exactly what break formats downstream.
- **Hit the corner cases.** Especially corrupt, truncated, and encrypted archives;
  wrong passwords; empty/zero-length members; unusual names and metadata; non-seekable
  sources. When porting or writing a reader, deliberately trigger each error path so the
  exception translator is exercised.
- **Use the declarative corpus.** Tests are driven by API-agnostic archive specs +
  expected data (generated on demand and cached); cross-validate against the `py7zr` /
  `rarfile` / frozen-DEV oracles where applicable (see
  `openspec/specs/testing-contract/spec.md`).
- **Fixing a bug? Red–green TDD.** First write a test that **reproduces** the bug and
  **fails**; then make it pass with the fix. The failing test is the proof the bug
  existed and that you fixed *that* bug.

### Coverage-guided fuzz (Atheris)

Atheris lives in the PEP 735 `fuzz` dependency group (`atheris`) and runs via
`.github/workflows/atheris-fuzz.yml` — same shape as the benchmark wall split:

- **Every PR:** short partitioned budgets over all targets (blocks the PR; sharded
  across parallel jobs because each target pays a large Atheris cold-start).
- **Nightly schedule:** full partition, but only if default-branch HEAD moved in the
  last ~3 days (commit-recency guard; dormant stretches skip the expensive run).
- **`workflow_dispatch`:** force the full partition (optional `budget_scale`).

Mutation fuzz (`tests/test_mutation_fuzz.py`) and `ARCHIVEY_FUZZ=1` /
`tests/fuzz_sevenzip_parser.py` / `tests/fuzz_rar_parser.py` stay as they are.

Local smoke (Linux; needs corpus fixture builders). Prefer Python 3.12 for current
Atheris wheels; on 3.11 ``uv`` resolves ``atheris`` 3.0.x::

    uv sync --group fuzz --group dev --extra all
    uv run --no-sync python -m tests.atheris_fuzz --smoke

    # or explicitly:
    uv sync --python 3.12 --group fuzz --group dev --extra all
    uv run --python 3.12 --no-sync python -m tests.atheris_fuzz --smoke

Deepen one target (budget seconds via env, e.g. `ARCHIVEY_FUZZ_BUDGET_SEVENZIP_HEADER=60`,
`ARCHIVEY_FUZZ_BUDGET_ZIP=60`, or `ARCHIVEY_FUZZ_BUDGET_UNIX_COMPRESS=60`)::

    uv run --no-sync python -m tests.atheris_fuzz --target sevenzip_header
    uv run --no-sync python -m tests.atheris_fuzz --target zip
    uv run --no-sync python -m tests.atheris_fuzz --target unix_compress
On a crash the harness writes the input under `artifacts/atheris/` and prints a one-line
repro command.

## Working with the specs (please read)

When you hit a **discrepancy** — specs disagreeing with the prose docs, the specs
disagreeing with each other, or the design simply not covering your case — **pause and
ask the maintainer** rather than silently picking an interpretation. A conflict usually
means a decision hasn't been made yet, and guessing bakes the wrong one into the code.
Surface it (an issue, a PR comment, or an `openspec/changes/` proposal) and let it be
decided explicitly.
