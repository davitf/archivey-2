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

The coordinator is a **pull-based sink**: it drives the reader (`cost`,
`get_members_if_available()`, `_iter_with_data()`/`stream_members()`) and picks an algorithm —
no push-model state machine, and **no upfront pass the run doesn't need** (it never calls
`members()` speculatively). Per member: `check_universal(original)` → policy transform on a
**transient copy** → optional user `filter` on the copy → write FILE/DIR/SYMLINK/HARDLINK.

**Symlinks** are created via `os.symlink()` and get the post-creation `Path.resolve()` escape
check with the `ELOOP`/`RuntimeError` guard. A symlink does **not** require its target to be
extracted (unlike a hardlink) — a symlink to a filtered-out/later/external target is created
and may dangle (only the within-`dest` escape check applies); no copy is made. If the
filesystem can't create symlinks (`os.symlink` raises), it's a per-member `OnError` failure —
**no** silent copy-the-target fallback (which is what `tarfile` does).

**Hardlinks** (the real file always precedes its links in TAR order; see `format-tar` MODIFIED
delta). One **core** algorithm, plus one **optional** optimization — no separate no-filter
path (that's just the core with zero orphans):

1. **Core — sequential pass + conditional second pass.** Record written FILEs in a per-source
   `{device → path}` map; a link to an already-written source uses `os.link()`. With no filter
   nothing is orphaned → one pass, done. If a filter orphans a selected link: seekable source →
   resolve all orphans in one second pass (only if an orphan appears; re-scan for plain,
   re-decompress ≤ 2× for compressed); forward-only source → per-member `OnError` failure. The
   coordinator never speculatively calls `members()` (a plain-tar scan isn't reliably cheap).
2. **Optional — planned single pass.** *If* filtering **and** a free member list is available
   (`get_members_if_available()` ≠ None: true index / already-materialized), plan up front and
   stage each needed source to the first selected link's path during the single pass, skipping
   the second pass. An optimization over the core, not a separate correctness path.

   Cross-device links prefer `os.link()` to a same-device sibling copy before `shutil.copy2`.
4. **Bomb tracking:** `BombTracker` on the **original** member; per-member ratio when
   `compressed_size` is known (ZIP); **archive-wide ratio** when outer
   `compressed_source_size` is known (compressed TAR — see spec delta).
5. **`OnError`:** `STOP` vs `CONTINUE` per spec (also governs unrecoverable orphaned links);
   cumulative bomb limit always stops.

Pull-model sink, not DEV's push-model helper: no `can_move_file`, no `process_file_extracted`,
no general deferred-state machine. The only bounded auxiliary state is the per-source
`{device → path}` map, the orphaned-link list awaiting the second pass (core), and the write
plan (optional optimization).

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
4. **Coordinator is a pull-based sink; one core hardlink algorithm + one optional
   optimization.** Core = sequential pass + conditional second pass; the no-filter case is
   just this with zero orphans (no separate implementation). A filter that orphans a link →
   one second pass on a seekable source (only if an orphan appears; never scan speculatively),
   or `OnError` failure on a forward-only source. Optional optimization = a planned single pass
   when filtering **and** a free member list exists (`get_members_if_available()` ≠ None).
   Cross-device links reuse a same-device sibling before copying. The old `no pending_*` gate
   is relaxed to "pull-model sink, no push-model state machine"; bounded maps (plan,
   `{device → path}`, orphan list) are fine.
5. **Minimal config** — bomb limits and policies as keyword args on `extract()` /
   `extract_all()`; full public config surface remains Phase 5.

## Specs

- **`safe-extraction`** (ADDED) — archive-wide decompression ratio when per-member
  `compressed_size` is unavailable but outer `compressed_source_size` is known; the
  `BombTracker` constructor gains a `compressed_source_size` argument.
- **`safe-extraction`** (ADDED) — symlink extraction is target-independent (dangling links
  allowed within `dest`, no copy) and fails safe via `OnError` on filesystems without symlink
  support (no silent copy-the-target fallback).
- **`safe-extraction`** (MODIFIED) — *Hardlink Two-Pass Extraction* reframed around the
  pull-based sink: unrecoverable orphaned links follow `OnError` (not an unconditional raise),
  cross-device links reuse a same-device sibling, and the excluded-source recovery mechanism
  is algorithm-selected (details in `format-tar`).
- **`format-tar`** (MODIFIED) — reconcile the TAR hardlink requirement with `safe-extraction`:
  pull-based sink; one core algorithm (sequential pass + conditional second pass, subsuming the
  no-filter case) plus an optional planned single pass when filtering and a free member list
  exists; `OnError` for forward-only orphans; cross-device sibling linking.

Implements (no other deltas) the rest of the `safe-extraction` spec and wires
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
- **Risk:** orphaned-link recovery (source filtered out) — the core sequential + conditional
  second pass (plus the optional planned-single-pass optimization) and cross-device sibling
  linking; follow the `format-tar` MODIFIED delta literally and add focused tests before broad
  corpus coverage.

## Implementation stages

1. **Types + filters** — enums, `ExtractionProgress`/`ExtractionResult`, `check_universal`,
   policy transforms; unit tests.
2. **Coordinator core** — FILE/DIR/SYMLINK write, overwrite policy, `OnError`, progress/
   results; ZIP extract vertical slice.
3. **Hardlinks + bombs** — the core sink (sequential pass + conditional second pass) with the
   optional planned single pass when a free list exists; per-source `{device → path}` map +
   cross-device sibling linking; `OnError` for forward-only orphans; `BombTracker` (per-member
   + archive-wide); adversarial corpus tests.
4. **Public API** — `archivey.extract()`, full `extract_all()` signature, retire frozen-
   oracle extraction coverage as tests transfer.

Each stage ends green (pyrefly + ty + ruff + its new tests).
