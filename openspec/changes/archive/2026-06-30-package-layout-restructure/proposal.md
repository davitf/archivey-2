# Package layout restructure (public surface at root, backends under internal)

## Why

The codebase drifted from `ARCHITECTURE.md` §1. Today:

- **Public types and entry points** (`types`, errors, cost, `open_archive`, `ArchiveReader`)
  live under `archivey.internal.*` and are re-exported from `__init__.py`.
- **Format backends** live at `archivey.formats.*` (package root) even though they are
  implementation-only — not in `__all__`, not a supported import path.
- **Codec streams** correctly live under `archivey.internal.streams.*`.

Phase 4 adds `extraction.py`, `filters.py`, and new public extraction types. Landing
those in the current layout would cement the wrong shape. This change realigns the tree
**before** `phase-4-tar-streaming` and `phase-4-safe-extraction` implementation.

## What Changes

### Target layout (implements ARCHITECTURE.md §1)

```
archivey/
├── __init__.py          # thin re-exports; __all__ unchanged for users
├── core.py              # open_archive, detect_format, format_availability, …
├── types.py             # ArchiveMember, ArchiveFormat, … (+ future extract types)
├── exceptions.py        # ArchiveyError hierarchy
├── cost.py              # CostReceipt, ListingCost, AccessCost, StreamCapability
├── reader.py            # ArchiveReader ABC only (public contract)
│
└── internal/            # unstable; not part of the public import surface
    ├── base_reader.py   # BaseArchiveReader + ReadBackend / WriteBackend ABCs
    ├── registry.py
    ├── detection.py     # engine (public wrappers stay on core.py)
    ├── naming.py, logs.py, config.py
    ├── extraction.py    # (Phase 4 — placeholder path only; not built in this change)
    ├── filters.py       # (Phase 4 — placeholder path only)
    ├── backends/        # ← was formats/
    │   ├── __init__.py  # registration imports
    │   ├── zip.py       # ← was zip_reader.py (optional rename for consistency)
    │   ├── tar.py
    │   └── …
    └── streams/         # unchanged
        └── streamtools/
```

### Moves (mechanical, no behavior change)

| From | To |
|------|-----|
| `internal/types.py` | `types.py` |
| `internal/errors.py` | `exceptions.py` |
| `internal/cost.py` | `cost.py` |
| `internal/open_archive.py` + registry query wrappers | `core.py` |
| `internal/reader.py` → public `ArchiveReader` | `reader.py` |
| `internal/reader.py` → `BaseArchiveReader`, backends | `internal/base_reader.py` |
| `internal/detection.py` | stays; `detect_format` / `FormatInfo` re-exported via `core.py` |
| `formats/*` | `internal/backends/*` |

### Decisions locked in

1. **Backends directory name** — `internal/backends/`, not `formats/`.
2. **Entry module name** — `core.py` (not `api.py`).
3. **No compatibility shims** — `archivey.internal.types` etc. are not preserved; v2 has
   no backwards-compatibility requirement. Update all in-repo imports.
4. **Dedicated PR** — merge before Phase 4 implementation starts.
5. **User-visible API unchanged** — same `import archivey` / `__all__` names; only
   internal paths change.

### Out of scope

- No public `archivey.streams` package.
- No split of `streamtools` into a separate distribution.
- No Phase 4 extraction implementation (only reserved paths in the layout).
- Optional backend file renames (`zip_reader.py` → `zip.py`) — allowed if it reduces
  churn with `backends/` but not required; tasks default to move-as-is unless rename is
  cheap.

## Specs

- **`packaging-and-extras`** (ADDED) — source package layout contract: public modules at
  package root; implementation under `internal/`; backends under `internal/backends/`.

Update `ARCHITECTURE.md` §1 to match the implemented tree (including `backends/`).

## Impact

- **Depends on:** nothing (merge after current main / open PRs as usual).
- **Blocks:** Phase 4 implementation (`phase-4-tar-streaming`, `phase-4-safe-extraction`)
  should start after this lands so new modules are created in the final locations.
- **Affected code:** all of `src/archivey/` import graph; tests; `ARCHITECTURE.md`;
  mkdocstrings paths if any reference old modules.
- **Risk:** large mechanical diff — mitigate with `test_public_api.py`, full pytest, and
  no logic edits in the same commits as moves (move-only commits where possible).

## Gates

- `uv run pyrefly check` + `uv run ty check` + `uv run ruff` clean.
- `uv run pytest` green.
- `archivey.__all__` unchanged (modulo any explicitly planned new exports — none in this change).
- `import archivey` still registers all bundled backends.
