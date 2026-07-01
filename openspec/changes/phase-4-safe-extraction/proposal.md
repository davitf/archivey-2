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

Single sequential forward pass over the reader's `_iter_with_data()` (or the ABC's
`stream_members()` wrapper) — **no upfront pre-pass** (a TAR has no central directory, so a
closure map would cost a full header scan or, for `.tar.gz`, a full extra decompression that
the common case never needs):

1. **Per member:** `check_universal(original)` → policy transform on a **transient copy**
   → optional user `filter` on the copy → write FILE/DIR/SYMLINK/HARDLINK per spec.
2. **Symlinks:** post-creation `Path.resolve()` check with `ELOOP`/`RuntimeError` guard.
3. **Hardlinks:** the real file always precedes any hardlink to it in TAR order. FILE members
   are recorded in a running per-source `{device → path}` map as they are written; a selected
   link to an already-written source is created with `os.link()` (the common case — no seek,
   no extra pass). A selected link whose source was **excluded** by the selector/`filter`
   (an "orphaned" link, only possible with a filter) recovers the source's content to the
   first selected link's path via a strategy chosen from the source's `CostReceipt` (see
   `format-tar` MODIFIED delta):
   - **forward-only** → per-member failure via `OnError` (no recovery possible);
   - **seekable `DIRECT`** (plain `.tar`) → seek back and materialize immediately;
   - **seekable `SOLID`** (compressed) → collect orphans, resolve in a **single second pass**,
     and only when an orphan actually exists.
   Cross-device links prefer `os.link()` to a same-device sibling copy before falling back to
   `shutil.copy2`. No `pending_*` deferred-creation machine.
4. **Bomb tracking:** `BombTracker` on the **original** member; per-member ratio when
   `compressed_size` is known (ZIP); **archive-wide ratio** when outer
   `compressed_source_size` is known (compressed TAR — see spec delta).
5. **`OnError`:** `STOP` vs `CONTINUE` per spec (also governs unrecoverable orphaned links);
   cumulative bomb limit always stops.

No DEV `pending_*` deferred-creation machine, no `can_move_file`, no `process_file_extracted`.
(The only bounded auxiliary state is the running per-source `{device → path}` map, and — for
the `SOLID` orphaned-link case only — a list of orphaned links awaiting the single second pass.)

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
4. **No hardlink pre-pass; orphaned-link recovery is chosen from the `CostReceipt`.** Default
   is a single sequential pass. A hardlink whose source was filtered out recovers via: seek
   (seekable `DIRECT` / plain tar), one deferred second pass (seekable `SOLID` / compressed
   tar, only when an orphan exists), or `OnError` failure (forward-only). The `pending_*` ban
   targets DEV's deferred link-creation state machine (`can_move_file`,
   `process_file_extracted`), not the bounded per-source map / orphan list used here.
5. **Minimal config** — bomb limits and policies as keyword args on `extract()` /
   `extract_all()`; full public config surface remains Phase 5.

## Specs

- **`safe-extraction`** (ADDED) — archive-wide decompression ratio when per-member
  `compressed_size` is unavailable but outer `compressed_source_size` is known; the
  `BombTracker` constructor gains a `compressed_source_size` argument.
- **`format-tar`** (MODIFIED) — reconcile the TAR hardlink requirement with `safe-extraction`:
  no upfront pre-pass; single sequential pass; orphaned-link recovery chosen from the
  `CostReceipt` (seek for `DIRECT`, one second pass for `SOLID`, `OnError` failure for
  forward-only); cross-device sibling linking.

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
- **Risk:** orphaned-link recovery (source filtered out) — three cost-driven paths (seek /
  second pass / `OnError` failure) plus cross-device sibling linking; follow the `format-tar`
  MODIFIED delta literally and add focused tests before broad corpus coverage.

## Implementation stages

1. **Types + filters** — enums, `ExtractionProgress`/`ExtractionResult`, `check_universal`,
   policy transforms; unit tests.
2. **Coordinator core** — FILE/DIR/SYMLINK write, overwrite policy, `OnError`, progress/
   results; ZIP extract vertical slice.
3. **Hardlinks + bombs** — sequential resolution with the running per-source `{device → path}`
   map; orphaned-link recovery (seek / second pass / `OnError`) + cross-device sibling
   linking; `BombTracker` (per-member + archive-wide); adversarial corpus tests.
4. **Public API** — `archivey.extract()`, full `extract_all()` signature, retire frozen-
   oracle extraction coverage as tests transfer.

Each stage ends green (pyrefly + ty + ruff + its new tests).
