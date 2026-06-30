# Phase 4b: Safe extraction (`ExtractionCoordinator` + bomb limits)

## Why

Phases 1–3 gave us read paths for ZIP, TAR (random-access), single-file, ISO, and
directory — but **nothing writes members to disk safely**. `extract_all()` is a stub
(`NotImplementedError`) and there is no `archivey.extract()` top-level helper.

This change implements the **`safe-extraction`** capability: a dedicated
`internal/extraction.py` module with an `ExtractionCoordinator` that drives one forward
pass over `(member, stream)` pairs, enforces universal path-safety filters, applies
`ExtractionPolicy` transforms, tracks decompression-bomb limits, and reports per-member
`ExtractionResult` / `on_progress` callbacks.

TAR forward-only streaming is **`phase-4-tar-streaming`** (separate change). This change
can land **first** against ZIP/directory/seekable TAR via the base `_iter_with_data()`
default; non-seekable `tar.gz` *extraction* needs both changes green.

## What Changes

### New modules (written fresh — do not port DEV's `ExtractionHelper`)

| Module | Contents |
|--------|----------|
| `internal/filters.py` | `check_universal()`, `POLICY_TRANSFORMS`, policy transforms |
| `internal/extraction.py` | `ExtractionCoordinator`, `BombTracker` |
| `internal/progress.py` (or `types`) | `ExtractionProgress`, `ExtractionResult`, `ExtractionStatus`, enums |

### `ExtractionCoordinator` algorithm (per `safe-extraction` spec, not the stale ARCHITECTURE sketch)

Single forward pass over the reader's `_iter_with_data()` (or the ABC's
`stream_members()` wrapper):

1. **Pre-pass (random-access only):** build hardlink closure map when the full member list
   is cheaply available (`_get_members_registered()` or equivalent).
2. **Per member:** `check_universal(original)` → policy transform on a **transient copy**
   → optional user `filter` on the copy → write FILE/DIR/SYMLINK/HARDLINK per spec.
3. **Symlinks:** post-creation `Path.resolve()` check with `ELOOP`/`RuntimeError` guard.
4. **Hardlinks:** TAR ordering guarantee in streaming mode; random-access excluded-source
   staging (first selected link path or hidden temp inside `dest`); cross-device
   `shutil.copy2` fallback.
5. **Bomb tracking:** `BombTracker` on the **original** member; per-member ratio when
   `compressed_size` is known (ZIP); **archive-wide ratio** when outer
   `compressed_source_size` is known (compressed TAR — see spec delta).
6. **`OnError`:** `STOP` vs `CONTINUE` per spec; cumulative bomb limit always stops.

No `pending_*` dicts. No `can_move_file`. No `process_file_extracted`.

### Public API

- `archivey.extract(source, dest, *, policy, overwrite, on_error, on_progress, max_extracted_bytes, max_ratio, …) -> list[ExtractionResult]`
- `ArchiveReader.extract_all(dest, *, members, filter, …) -> list[ExtractionResult]` —
  delegates to the same coordinator.
- Enums: `ExtractionPolicy`, `OverwritePolicy`, `OnError` exported from public API.

`read()` / `open()` remain **without** bomb limits (per spec).

### Archive-wide bomb ratio (decided)

When `member.compressed_size` is unknown (TAR members) but the reader exposes
`compressed_source_size` (from `phase-4-tar-streaming`), apply the ratio check as:

```
cumulative_bytes_written / compressed_source_size > max_ratio
```

after the activation threshold (default 5 MiB), in addition to the existing per-member
check. Skip when the denominator is unknown (pipes, plain `.tar`).

## Decisions locked in this change

1. **Coordinator consumes `_iter_with_data()`**, not a separate `open_fn` + member list
   (aligns with `safe-extraction` spec; supersedes the older ARCHITECTURE §2.6 sketch).
2. **Archive-wide ratio for solid containers** when outer compressed size is known.
3. **No streaming TAR work here** — only consumes it once `phase-4-tar-streaming` lands.
4. **Minimal config** — bomb limits and policies as keyword args on `extract()` /
   `extract_all()`; full public config surface remains Phase 5.

## Specs

- **`safe-extraction`** (ADDED) — archive-wide decompression ratio when per-member
  `compressed_size` is unavailable but outer `compressed_source_size` is known.

Implements (no other deltas) the full `safe-extraction` spec and wires
`archive-reading` `extract_all`. Tests cover `testing-contract` adversarial scenarios
(path traversal, zip bomb) and extraction scenarios across ZIP + seekable TAR.

## Impact

- **Depends on:** Phase 3 green (at least one indexed backend to extract from — ZIP is the
  vertical slice).
- **Coordinates with:** `phase-4-tar-streaming` (`compressed_source_size`, non-seekable
  `_iter_with_data()` for pipe `tar.gz` extract).
- **Affected code:** new `internal/filters.py`, `internal/extraction.py`,
  `internal/progress.py`; `internal/reader.py` (`extract_all` body); `__init__.py`
  (`extract()`, new types); adversarial fixtures + `tests/test_extraction.py`.
- **Risk:** hardlink random-access excluded-source staging — follow `safe-extraction`
  spec literally; add focused tests before broad corpus coverage.

## Implementation stages

1. **Types + filters** — enums, `ExtractionProgress`/`ExtractionResult`, `check_universal`,
   policy transforms; unit tests.
2. **Coordinator core** — FILE/DIR/SYMLINK write, overwrite policy, `OnError`, progress/
   results; ZIP extract vertical slice.
3. **Hardlinks + bombs** — two-pass hardlink resolution, `BombTracker` (per-member +
   archive-wide), adversarial corpus tests.
4. **Public API** — `archivey.extract()`, full `extract_all()` signature, retire frozen-
   oracle extraction coverage as tests transfer.

Each stage ends green (pyrefly + ty + ruff + its new tests).
