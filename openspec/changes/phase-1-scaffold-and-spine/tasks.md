# Tasks — Phase 1: Project scaffold, spine, and test harness

> Run tools through uv: `uv sync`, `uv run mypy`, `uv run pytest`, `uv run ruff`.
> The package stays pip-installable; uv is the workflow, not a dependency.
>
> Clean-slate: the spine is written **fresh** to `ARCHITECTURE.md` / `SPEC.md`;
> `archivey-dev` is reference + frozen oracle only, not a copy baseline.

## 1. Obtain the DEV source (reference + oracle)

> A plain HTTPS `git clone` works from this environment. The GitHub API / WebFetch
> is unauthenticated-rate-limited and returns `403` — do not use it to conclude the
> repo is private. See the "Reference repository" section of the root `CLAUDE.md`.

- [ ] 1.1 Clone DEV into a scratch location outside the project tree:
      `git clone https://github.com/davitf/archivey-dev.git /tmp/archivey-dev`.
- [ ] 1.2 Check out a pinned commit SHA (known-good:
      `730275b7a755f8b5b8d08d3d4d9b267b5bdadb0d`) so references are reproducible.
- [ ] 1.3 Record the source SHA in the Phase 1 commit message / PR for traceability.
- [ ] 1.4 If the clone fails, retry over HTTPS with backoff; only then surface the
      blocker. DEV is used here as the frozen oracle and as the reference for
      porting leaf logic in later phases — it is **not** copied wholesale.

## 2. Project configuration

- [ ] 2.1 `pyproject.toml` with `[build-system]` = `hatchling`.
- [ ] 2.2 `[project]`: name `archivey`, version `0.2.0.dev0`,
      `requires-python = ">=3.11"`, description, license, readme.
- [ ] 2.3 `[project.optional-dependencies]` **exactly** per `packaging-and-extras`:
      `7z`, `rar`, `crypto`, `7z-write`, `iso`, `zstd`, `lz4`, `cli`, `seekable`,
      `recommended-lite`, `recommended`, `all` (the spec's table is the source of
      truth for each extra's dependency list and the union definitions).
- [ ] 2.4 `[dependency-groups]` `dev` (PEP 735): pytest, mypy, ruff, coverage, the
      archive-generation libs, **and the test oracles `py7zr` + `rarfile`**.
- [ ] 2.5 Tool config: `[tool.mypy]` `strict = true`, `python_version = "3.11"`;
      `[tool.ruff]`; `[tool.coverage]`.
- [ ] 2.6 `.python-version` (`3.11`) and any `[tool.uv]` settings.
- [ ] 2.7 Generate and commit `uv.lock`.

## 3. Package layout & logging

- [ ] 3.1 `src/archivey/{internal,formats}/` with `__init__.py` files; public
      `src/archivey/__init__.py`; add `py.typed` (PEP 561).
- [ ] 3.2 Establish the `archivey` logger hierarchy; the library installs **no**
      handlers and emits nothing by default (`logging` spec).

## 4. Spine — written fresh (no format backends yet)

- [ ] 4.1 `BaseArchiveReader` ABC in `ARCHITECTURE.md` vocabulary: `_iter_members`,
      `_iter_with_data`, `_open_member` (**no** `for_iteration`), class attributes
      `_SUPPORTS_RANDOM_ACCESS` / `_MEMBER_LIST_UPFRONT` (TAR resolves the former at
      `__init__` from source seekability), **no** `_prepare_member_for_open` hook;
      registration + link-resolution (depth-limited) skeleton.
- [ ] 4.2 Backend registry + `Backend` ABC: import-time self-registration;
      selection by peek bytes / path / intent; `SUPPORTS_WRITE` / `REQUIRES_SEEK`.
- [ ] 4.3 Public-API skeleton: `open_archive()`, the `ArchiveReader` surface, and
      the context-manager / `close()` lifecycle.
- [ ] 4.4 Data model: `Member` (frozen, hashable, `extra` excluded from hash/eq,
      digests under algorithm keys), `ArchiveInfo`, `ArchiveFormat`, `MemberType`,
      compression-method model, and member-name normalization rules.
- [ ] 4.5 `ArchiveyError` hierarchy (`error-handling` spec): single root, required
      attributes (`format`, member name), cause/traceback preservation contract.
- [ ] 4.6 `Intent` enum + `CostReceipt` (`ListingCost`/`AccessCost`/
      `StreamCapability`) types.

> These are contracts/types only — with no format backend wired, opening a real
> archive is not yet supported (paths raise a clear not-implemented error or the
> registry reports "no backend"). Behavior is filled in Phases 2–7.

## 5. New declarative test framework

- [ ] 5.1 Port the corpus cleaned: `sample_archives.py`, `ArchiveContents`,
      `FileInfo`, `ArchiveCreationInfo` (declarative specs + expected data, which are
      API-agnostic).
- [ ] 5.2 `conftest.py` parametrization; **generate-on-demand + cache** to
      `$XDG_CACHE_HOME/archivey-tests/` keyed by
      `hash(spec + creation_params + lib versions)`; `--regen` flag.
- [ ] 5.3 `tests/fixtures/` for committed adversarial archives, each with a JSON
      sidecar (format per `ARCHITECTURE.md §2.8`); add generated archives to
      `.gitignore`; **commit no generated binaries**.
- [ ] 5.4 Flat `tests/` layout (no `tests/archivey/` nesting).
- [ ] 5.5 Clone DEV's suite into `tests/_dev_oracle/` as a **frozen, read-only
      regression gate** — collected and run, never refactored, allowed to skip/xfail
      as the new API diverges. (Deleted in Phase 10.)

## 6. Verify — acceptance criteria

**Spec scenarios covered**
- [ ] 6.1 `packaging-and-extras`: *core install pulls no third-party packages*,
      *install rejected on unsupported Python*, *supported on all three operating
      systems*, *`__version__` reflects the installed distribution*.
- [ ] 6.2 `backend-registry`: *core backend available without extras*, *optional
      backend absent at import* (registry exists; no format backends yet).
- [ ] 6.3 `logging`: *library emits no output by default*.
- [ ] 6.4 `testing-contract`: framework stands up (matrix harness importable; oracle
      hooks wired but skipped when libs absent).

**Gates**
- [ ] 6.5 `uv run mypy src/` clean under `--strict`.
- [ ] 6.6 `uv run ruff check` clean.
- [ ] 6.7 `uv run pytest tests/` green (mostly skips at this stage).
- [ ] 6.8 `git status` clean after a test run (no new binary files).
- [ ] 6.9 `archivey.__version__` resolves via `importlib.metadata`.

## 7. Deferred (not in this phase)

- Stream layer (`internal/streams/`, codec layer) — Phase 2.
- Leaf format backends (ZIP/dir/single-file/ISO) — Phase 3.
- Native 7z/RAR readers — Phase 7.
- CI matrix + coverage gating — Phase 10.
- Deleting `tests/_dev_oracle/` — Phase 10.
