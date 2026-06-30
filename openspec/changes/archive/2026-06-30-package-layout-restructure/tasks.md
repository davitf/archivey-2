# Tasks — Package layout restructure

> Run tools through uv: `uv run pytest`, `uv run pyrefly check`, `uv run ty check`,
> `uv run ruff`.
> Prerequisite: none (dedicated PR before Phase 4).
> **Behavior-preserving:** move/rename imports only — no logic changes mixed into move commits.

## 0. Decisions (honored below)

- [x] 0.1 Public modules at package root: `core.py`, `types.py`, `exceptions.py`,
      `cost.py`, `reader.py`.
- [x] 0.2 Backends under `internal/backends/` (was `formats/`).
- [x] 0.3 `BaseArchiveReader` + `ReadBackend` in `internal/base_reader.py`; public
      `ArchiveReader` in `reader.py`.
- [x] 0.4 No compatibility shims for old `archivey.internal.*` / `archivey.formats.*` paths.
- [x] 0.5 `archivey.__all__` unchanged for end users.

## 1. Hoist public modules to package root

- [x] 1.1 `internal/types.py` → `types.py`; update imports across `src/` and `tests/`.
- [x] 1.2 `internal/errors.py` → `exceptions.py`.
- [x] 1.3 `internal/cost.py` → `cost.py`.
- [x] 1.4 Split `internal/reader.py`:
      - `reader.py` — public `ArchiveReader` ABC (+ `MemberSelector` alias if public).
      - `internal/base_reader.py` — `BaseArchiveReader`, `ReadBackend`, `WriteBackend`,
        `ArchiveWriter` placeholder.
- [x] 1.5 Create `core.py` — `open_archive`, `source_name`, `detect_format`, `FormatInfo`,
      `DetectionConfidence`, `format_availability`, `list_supported_formats`,
      `list_known_formats`, registry types used publicly (`FormatSupport`, etc.).
- [x] 1.6 Thin `__init__.py` — re-export from `core`, `types`, `exceptions`, `cost`,
      `reader` only (no `from archivey.internal…` in `__init__` except backend registration
      trigger).

## 2. Move backends

- [x] 2.1 `formats/` → `internal/backends/` (all reader modules + `__init__.py`).
- [x] 2.2 Update `internal/backends/__init__.py` registration imports; root `__init__.py`
      triggers registration via `import archivey.internal.backends` (or equivalent).
- [x] 2.3 Update every backend module's imports to new paths (`base_reader`, `types`, …).
- [x] 2.4 Grep gate: no remaining `archivey.formats` imports in `src/` or `tests/`
      (exclude `tests/_dev_oracle/` if frozen).

## 3. Internal spine cleanup

- [x] 3.1 Update `internal/detection.py`, `internal/registry.py`, `internal/naming.py`,
      `internal/logs.py`, `internal/config.py`, `internal/streams/*` imports.
- [x] 3.2 Remove emptied old files (`internal/types.py`, `internal/errors.py`, …) after
      moves.
- [x] 3.3 Optional: rename `zip_reader.py` → `zip.py` etc. under `backends/` if done in
      the same PR without extra behavior change. *(Skipped — move-as-is.)*

## 4. Tests and docs

- [x] 4.1 Update test imports (`test_public_api.py` must stay green — primary guard).
- [x] 4.2 Update `ARCHITECTURE.md` §1 module tree to match (including `backends/`).
- [x] 4.3 Update mkdocstrings / doc paths if they reference old module paths. *(None found.)*
- [x] 4.4 Update Phase 4 change proposals/tasks if they reference old paths (`formats/`,
      `internal/types.py`) — pointer only, no Phase 4 implementation.

## 5. Gates

- [x] 5.1 `uv run pyrefly check` + `uv run ty check` + `uv run ruff` clean.
- [x] 5.2 `uv run pytest` green.
- [x] 5.3 `test_public_api.py` — `__all__` stable; `ArchiveReader` abstract; no internal
      hooks on public `ArchiveReader`.
- [x] 5.4 `import archivey` registers backends (`list_supported_formats()` non-empty).
