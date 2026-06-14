# Tasks — Phase 1: Project scaffold and initial port

> Run tools through uv: `uv sync`, `uv run mypy`, `uv run pytest`, `uv run ruff`.
> The package stays pip-installable; uv is the workflow, not a dependency.

## 1. Project configuration

- [ ] 1.1 Create `pyproject.toml` with `[build-system]` using `hatchling`.
- [ ] 1.2 `[project]` metadata: name `archivey`, version `0.2.0.dev0`,
      `requires-python = ">=3.11"`, description, license, readme.
- [ ] 1.3 `[project.optional-dependencies]` runtime extras:
      `7z` (py7zr), `rar` (rarfile), `iso` (pycdlib), `zstd` (zstandard),
      `lz4` (lz4), `cli` (tqdm), and `all` (aggregates every runtime extra).
- [ ] 1.4 `[dependency-groups]` `dev` (PEP 735): pytest, mypy, ruff, coverage,
      plus any libs needed to generate test archives.
- [ ] 1.5 Tool config: `[tool.mypy]` `strict = true`, `python_version = "3.11"`;
      `[tool.ruff]`; `[tool.coverage]`.
- [ ] 1.6 Add `.python-version` (`3.11`) and any `[tool.uv]` settings needed.
- [ ] 1.7 Generate and commit `uv.lock` (`uv lock`).

## 2. Port source from DEV

- [ ] 2.1 Copy all `src/archivey/*.py` from `archivey-dev` verbatim.
- [ ] 2.2 Add `src/archivey/py.typed` (PEP 561 marker) if not present.
- [ ] 2.3 `uv sync` and confirm the package imports
      (`uv run python -c "import archivey"`).

## 3. Port test infrastructure

- [ ] 3.1 Copy `sample_archives.py`, `create_archives.py`, `conftest.py`,
      `testing_utils.py`, and all `test_*.py` from DEV.
- [ ] 3.2 Move committed binary test archives into `tests/fixtures/`.
- [ ] 3.3 Drop or quarantine (with a clear `skip` reason) tests that depend on
      DEV-only API this repo will not carry forward.

## 4. Verify — acceptance criteria

- [ ] 4.1 `uv run mypy src/` passes under `--strict`.
- [ ] 4.2 `uv run pytest tests/` passes (minus the DEV-only tests from 3.3).
- [ ] 4.3 `uv run ruff check` passes.
- [ ] 4.4 Packaging sanity (per `packaging-and-extras` spec):
      bare install pulls no third-party runtime deps; each extra installs only
      its own dependency; `uv pip install -e .` and a plain `pip install .` both
      succeed.
- [ ] 4.5 `archivey.__version__` resolves via `importlib.metadata`.
- [ ] 4.6 Confirm no new public API is exposed yet (private fork baseline).

## 5. Deferred (not in this phase)

- CI matrix (Python 3.11–3.13 × Linux/Windows) and coverage gating — Phase 10.
- Interface cleanups, public-API renames, fixture/JSON-sidecar overhaul —
  Phases 2–6.
