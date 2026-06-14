# Tasks — Phase 1: Project scaffold and initial port

> Run tools through uv: `uv sync`, `uv run mypy`, `uv run pytest`, `uv run ruff`.
> The package stays pip-installable; uv is the workflow, not a dependency.
>
> This phase ports from the existing `archivey-dev` codebase. The implementing
> agent runs in a fresh environment, so obtaining that source is the first task —
> do not assume it is already present.

## 1. Obtain the DEV source

> Access is confirmed working: a plain HTTPS `git clone` succeeds from this
> environment. (The GitHub API / WebFetch is unauthenticated-rate-limited and
> returns `403` — do not use it to conclude the repo is private.) See the
> "Reference repository" section of the root `CLAUDE.md`.

- [ ] 1.1 Clone the DEV repository into a scratch location outside the project
      tree: `git clone https://github.com/davitf/archivey-dev.git /tmp/archivey-dev`.
- [ ] 1.2 Check out a pinned, reproducible commit SHA rather than tracking the
      default branch, so the port is repeatable. Known-good revision:
      `730275b7a755f8b5b8d08d3d4d9b267b5bdadb0d` (default-branch HEAD when these
      specs were authored; the clone carries no release tags). A newer SHA may be
      chosen deliberately.
- [ ] 1.3 Record the exact source commit SHA in the Phase 1 commit message / PR
      description for traceability.
- [ ] 1.4 If the clone fails, retry over HTTPS (with backoff); only if it still
      fails, stop and surface the blocker — every later step depends on this source.

## 2. Project configuration

- [ ] 2.1 Create `pyproject.toml` with `[build-system]` using `hatchling`.
- [ ] 2.2 `[project]` metadata: name `archivey`, version `0.2.0.dev0`,
      `requires-python = ">=3.11"`, description, license, readme.
- [ ] 2.3 `[project.optional-dependencies]` runtime extras:
      `7z` (py7zr), `rar` (rarfile), `iso` (pycdlib), `zstd` (zstandard),
      `lz4` (lz4), `cli` (tqdm), and `all` (aggregates every runtime extra).
- [ ] 2.4 `[dependency-groups]` `dev` (PEP 735): pytest, mypy, ruff, coverage,
      plus any libs needed to generate test archives.
- [ ] 2.5 Tool config: `[tool.mypy]` `strict = true`, `python_version = "3.11"`;
      `[tool.ruff]`; `[tool.coverage]`.
- [ ] 2.6 Add `.python-version` (`3.11`) and any `[tool.uv]` settings needed.
- [ ] 2.7 Generate and commit `uv.lock` (`uv lock`).

## 3. Port source from DEV

- [ ] 3.1 Copy all `src/archivey/*.py` from the DEV checkout (step 1) verbatim
      into this project's `src/archivey/`.
- [ ] 3.2 Add `src/archivey/py.typed` (PEP 561 marker) if not present.
- [ ] 3.3 `uv sync` and confirm the package imports
      (`uv run python -c "import archivey"`).

## 4. Port test infrastructure

- [ ] 4.1 Copy `sample_archives.py`, `create_archives.py`, `conftest.py`,
      `testing_utils.py`, and all `test_*.py` from the DEV checkout.
- [ ] 4.2 Move committed binary test archives into `tests/fixtures/`.
- [ ] 4.3 Drop or quarantine (with a clear `skip` reason) tests that depend on
      DEV-only API this repo will not carry forward.

## 5. Verify — acceptance criteria

- [ ] 5.1 `uv run mypy src/` passes under `--strict`.
- [ ] 5.2 `uv run pytest tests/` passes (minus the DEV-only tests from 4.3).
- [ ] 5.3 `uv run ruff check` passes.
- [ ] 5.4 Packaging sanity (per `packaging-and-extras` spec):
      bare install pulls no third-party runtime deps; each extra installs only
      its own dependency; `uv pip install -e .` and a plain `pip install .` both
      succeed.
- [ ] 5.5 `archivey.__version__` resolves via `importlib.metadata`.
- [ ] 5.6 Confirm no new public API is exposed yet (private fork baseline).

## 6. Deferred (not in this phase)

- CI matrix (Python 3.11–3.13 × Linux/Windows) and coverage gating — Phase 10.
- Interface cleanups, public-API renames, fixture/JSON-sidecar overhaul —
  Phases 2–6.
