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

TAR forward-only streaming is **`phase-4-tar-streaming`** (separate change), which lands
**first**. This change builds on it: ZIP/directory/seekable TAR extraction works through the
base `_iter_with_data()` default, and the archive-wide bomb ratio consumes the
`compressed_source_size` hook that `phase-4-tar-streaming` adds. Non-seekable `tar.gz`
*extraction* additionally needs `phase-4-tar-streaming`'s forward-only `_iter_with_data()`
override.

## What Changes

### New modules (written fresh — do not port DEV's `ExtractionHelper`)

| Module | Contents |
|--------|----------|
| `src/archivey/internal/filters.py` | `check_universal()`, `POLICY_TRANSFORMS`, policy transforms |
| `src/archivey/internal/extraction.py` | `ExtractionCoordinator`, `BombTracker` |
| `src/archivey/internal/progress.py` (or `types.py`) | `ExtractionProgress`, `ExtractionResult`, `ExtractionStatus`, enums |

### `ExtractionCoordinator` algorithm (per `safe-extraction` spec, not the stale ARCHITECTURE sketch)

Single forward pass over the reader's `_iter_with_data()` (or the ABC's
`stream_members()` wrapper):

1. **Pre-pass (random-access only):** build hardlink closure map when the full member list
   is cheaply available (`_get_members_registered()` or equivalent).
2. **Per member:** `check_universal(original)` → policy transform on a **transient copy**
   → optional user `filter` on the copy → write FILE/DIR/SYMLINK/HARDLINK per spec.
3. **Symlinks:** post-creation `Path.resolve()` check with `ELOOP`/`RuntimeError` guard.
4. **Hardlinks:** the real file always precedes any hardlink to it in TAR order, so a single
   forward pass suffices — **no seek-back and no re-decompression**, which matters for solid
   `.tar.gz` where seeking to an earlier member would mean re-inflating from the start.
   - **Random-access** (member list known up front; for TAR the `linkname` is in the header,
     so the closure map is built from the same header scan that produces the index — no
     payload reads): when the forward pass reaches an **excluded-but-needed** source, stage
     its content **then** — write it to the first selected link's destination path (the bytes
     are streaming past us anyway), and `os.link()` further selected links to it when reached.
     The excluded source's own name is never created. The only state is a bounded
     `{source → first-selected-link path}` map drained during the pass — **not** DEV's
     `pending_*` deferred-creation machine.
   - **Streaming** (no upfront list): a selected hardlink whose source was filtered out cannot
     be recovered in one pass → raise an explicit `ExtractionError`. (Extract-all on a pipe
     resolves every hardlink; only partial selection that splits a group hits this.)
   - Cross-device `os.link()` failure falls back to `shutil.copy2` in both modes.
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
`compressed_source_size` (added by `phase-4-tar-streaming`, which lands first), apply the
ratio check as:

```
cumulative_bytes_written / compressed_source_size > max_ratio
```

once the **cumulative** output (`_total_bytes`, not per-member bytes) crosses the activation
threshold (default 5 MiB), in addition to the existing per-member check. Skip when the
denominator is unknown (pipes, plain `.tar`).

## Decisions locked in this change

1. **Coordinator consumes `_iter_with_data()`**, not a separate `open_fn` + member list
   (aligns with `safe-extraction` spec; supersedes the older ARCHITECTURE §2.6 sketch).
2. **Archive-wide ratio for solid containers** when outer compressed size is known.
3. **No streaming TAR work here** — `phase-4-tar-streaming` lands first; this change only
   consumes its `_iter_with_data()` override and `compressed_source_size` hook.
4. **Hardlink-to-excluded-source is staged forward** — in random-access mode the source is
   written to the first selected link's path **as the single forward pass reaches the source**
   (no seek-back, no re-decompression, no second pass); streaming raises if the source was
   filtered. The bounded `{source → link path}` staging map is permitted; the `pending_*`
   ban targets DEV's deferred link-creation state machine (`can_move_file`,
   `process_file_extracted`), not this map.
5. **Minimal config** — bomb limits and policies as keyword args on `extract()` /
   `extract_all()`; full public config surface remains Phase 5.

## Specs

- **`safe-extraction`** (ADDED) — archive-wide decompression ratio when per-member
  `compressed_size` is unavailable but outer `compressed_source_size` is known; the
  `BombTracker` constructor gains a `compressed_source_size` argument.
- **`format-tar`** (MODIFIED) — reconcile the TAR hardlink requirement with `safe-extraction`:
  the real file precedes its hardlinks (no deferred post-pass), streaming raises when the
  source was filtered out, random-access resolves at the first selected link.

Implements (no other deltas) the full `safe-extraction` spec and wires
`archive-reading` `extract_all`. Tests cover `testing-contract` adversarial scenarios
(path traversal, zip bomb) and extraction scenarios across ZIP + seekable TAR.

## Impact

- **Depends on:** Phase 3 green (at least one indexed backend to extract from — ZIP is the
  vertical slice) **and `phase-4-tar-streaming`** (lands first; provides
  `compressed_source_size` and the forward-only `_iter_with_data()` override).
- **Coordinates with:** `phase-4-tar-streaming` (`compressed_source_size`, non-seekable
  `_iter_with_data()` for pipe `tar.gz` extract).
- **Affected code:** new `src/archivey/internal/filters.py`,
  `src/archivey/internal/extraction.py`, `src/archivey/internal/progress.py`;
  `src/archivey/internal/base_reader.py` (`extract_all` body, replacing the
  `NotImplementedError` stub); `src/archivey/__init__.py` and `src/archivey/core.py`
  (`extract()`, new public types); adversarial fixtures + `tests/test_extraction.py`.
- **Risk:** hardlink random-access excluded-source staging — follow `safe-extraction`
  spec literally; add focused tests before broad corpus coverage.

## Implementation stages

1. **Types + filters** — enums, `ExtractionProgress`/`ExtractionResult`, `check_universal`,
   policy transforms; unit tests.
2. **Coordinator core** — FILE/DIR/SYMLINK write, overwrite policy, `OnError`, progress/
   results; ZIP extract vertical slice.
3. **Hardlinks + bombs** — pre-pass closure map (random-access) + immediate resolution at
   the first selected link; streaming raises on filtered-out source; `BombTracker`
   (per-member + archive-wide), adversarial corpus tests.
4. **Public API** — `archivey.extract()`, full `extract_all()` signature, retire frozen-
   oracle extraction coverage as tests transfer.

Each stage ends green (pyrefly + ty + ruff + its new tests).
