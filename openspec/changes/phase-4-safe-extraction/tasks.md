# Tasks — Phase 4b: Safe extraction (`ExtractionCoordinator` + bomb limits)

> Run tools through uv: `uv run pytest`, `uv run pyrefly check`, `uv run ty check`,
> `uv run ruff`.
> Prerequisites: Phase 3 complete (ZIP backend is the first extract vertical slice) **and
> `phase-4-tar-streaming` merged first** (provides `compressed_source_size` and the
> forward-only `_iter_with_data()` override this change consumes).
> Clean-slate: write `ExtractionCoordinator` fresh as a **pull-based sink** that drives the
> `ArchiveReader` (`cost`, `get_members_if_available()`, `members()`, `stream_members()` /
> `_iter_with_data()`) and selects an algorithm — do **not** port DEV's push-model
> `ExtractionHelper`. DEV's state sprawl came from the push model; the pull-model sink keeps
> only bounded, purpose-specific maps.
> Module paths below are post-`package-layout-restructure`: public API at
> `src/archivey/` root, internals under `src/archivey/internal/`.

> **DEV source map** (reference only, pin `730275b…`): `internal/extraction.py` /
> `filters.py` in DEV for behavioral edge cases to re-derive tests from — not for
> copy-paste.

## 0. Decisions locked in this change (no code, just honored below)

- [ ] 0.1 **Pull-based sink** driving `_iter_with_data()` `(member, stream)` pairs — algorithm
      selected from `get_members_if_available()` + `cost`; follows `safe-extraction` spec, not
      ARCHITECTURE §2.6's old `open_fn` sketch.
- [ ] 0.2 **Archive-wide bomb ratio** when `compressed_source_size` is known (spec delta);
      per-member ratio when `member.compressed_size` is known (ZIP).
- [ ] 0.3 **Transforms on transient copy**; `BombTracker` + `ExtractionResult` use
      **original** member.
- [ ] 0.4 **Pull-model sink, no push-model state machine** — bounded maps (plan, per-source
      `{device → path}`, orphan list) are fine; do not reintroduce DEV's `pending_*` /
      `can_move_file` / `process_file_extracted` sprawl.
- [ ] 0.5 **Bomb limits only on extract paths** — `read()` / `open()` unchanged.

## 1. Types and filters

- [ ] 1.1 **Enums** — `ExtractionPolicy`, `OverwritePolicy`, `OnError`, `ExtractionStatus`
      (public exports in `__init__.py`).
- [ ] 1.2 **Progress/result types** — `ExtractionProgress`, `ExtractionResult` dataclasses
      (`src/archivey/internal/progress.py` or `src/archivey/internal/types.py` per existing
      conventions).
- [ ] 1.3 **`src/archivey/internal/filters.py`** — `check_universal()` (path traversal, absolute paths,
      null bytes, symlink/hardlink escape at planning time, `MemberType.OTHER`);
      `transform_strict` / `transform_standard` / identity for `TRUSTED`;
      `POLICY_TRANSFORMS` dict.
- [ ] 1.4 **Filter unit tests** — universal rejections; STRICT strips execute/normalizes;
      STANDARD preserves execute; TRUSTED still rejects `..` paths.

## 2. `BombTracker`

- [ ] 2.1 **Implement `BombTracker`** per `safe-extraction` spec — cumulative
      `max_extracted_bytes`, per-member ratio with activation threshold, **archive-wide
      ratio** using the new optional `compressed_source_size` constructor arg (coordinator
      reads `reader.compressed_source_size` and passes it in). Archive-wide ratio activates
      on **cumulative** output (`_total_bytes`), not per-member.
- [ ] 2.2 **Tests** — cumulative limit; per-member ratio (ZIP fixture); activation
      threshold false-positive guard; archive-wide ratio with known outer size; skip when
      denominators unknown.

## 3. `ExtractionCoordinator` core

- [ ] 3.1 **`src/archivey/internal/extraction.py`** — `ExtractionCoordinator.run(reader, dest, …)`
      as a pull-based sink: inspect `reader.cost` + `reader.get_members_if_available()` to pick
      the hardlink algorithm (§4), then drive `reader._iter_with_data()` (respect `members`
      selector + user `filter` on transient copy).
- [ ] 3.2 **FILE extraction** — chunked copy with `BombTracker.count()`; apply mode/mtime
      best-effort after write; honor `OverwritePolicy`.
- [ ] 3.3 **DIR extraction** — `mkdir` with policy mode.
- [ ] 3.4 **SYMLINK extraction** — `os.symlink` + post-creation resolve check with
      `ELOOP`/`RuntimeError` guard (per spec pseudocode). Target-independent: a symlink to a
      filtered-out/later/external target is created and may dangle (only the within-`dest`
      escape check applies), no copy. `os.symlink` failure on an unsupported filesystem →
      per-member `OnError` failure; **no** copy-the-target fallback.
- [ ] 3.5 **`OnError.STOP` vs `CONTINUE`** — partial file cleanup; `ExtractionResult` with
      `FAILED`/`REJECTED` + `error`; cumulative bomb always stops.
- [ ] 3.6 **`on_progress` callback** — once per member with counters from spec.
- [ ] 3.7 **Wire `ArchiveReader.extract_all()`** — replace `NotImplementedError`; return
      `list[ExtractionResult]`.

## 4. Hardlinks (source precedes its links in TAR order; algorithm-selected — see `format-tar` MODIFIED delta)

- [ ] 4.1 **Algorithm selection** — only a selector/`filter` can orphan a link, so use a member
      list up front **only when it's free** (`get_members_if_available()` ≠ None). Never call
      `members()` speculatively (a plain-tar header scan isn't reliably cheap; compressed-tar
      listing would decompress everything). No free list → reactive handling (task 4.4).
- [ ] 4.2 **(A) No filter → single sequential pass** — record each written FILE under a
      per-source `{device → on-disk path}` map; a link to an already-written source uses
      `os.link` to a same-device copy. No planning, no second pass.
- [ ] 4.3 **(B) Filter + free list → planned single pass** — when `get_members_if_available()`
      returns the list, plan selection + policy + filter up front into a
      `source → selected-link-paths` map; one forward pass writes selected members and stages
      each needed (even excluded) source to the first selected link's path as it is reached;
      `os.link` the rest. No second pass.
- [ ] 4.4 **(C) Filter + no free list (plain `.tar` and compressed tar) → sequential +
      conditional second pass** — collect orphaned links during the main pass; resolve all in
      **one** second pass on a seekable source, only if an orphan exists (re-scan for plain;
      re-decompress ≤ 2× for compressed). Forward-only orphan → per-member `OnError` failure
      (STOP raises / CONTINUE records `FAILED`); no recovery.
- [ ] 4.5 **Cross-device sibling linking** — prefer `os.link` to an existing same-device copy of
      the source; only `shutil.copy2` when none exists, then record the copy's device so later
      same-device links reuse it (better than `tarfile`, which recopies from the archive per link).
- [ ] 4.6 **Tests** — unfiltered → single pass, no list fetched; filter + free-list (indexed /
      already-materialized) orphan → planned single pass, no second pass; filter + plain-tar
      orphan → one re-scan second pass; filter + compressed-tar orphan → one second pass (assert
      decompressed ≤ 2×); filter + no orphan → single pass, no speculative list; forward-only
      orphan → `OnError` STOP/CONTINUE; chained cross-device link reuses the sibling copy (mock
      devices / `os.link` `EXDEV`).
- [ ] 4.7 **Symlink tests** — dangling symlink to a filtered-out target created within `dest`
      (no copy, no error); `os.symlink` failure on an unsupported filesystem → `OnError`
      STOP/CONTINUE (no copy-the-target fallback).

## 5. Public API + ZIP vertical slice

- [ ] 5.1 **`archivey.extract()`** — top-level one-shot API per spec (no member selector).
- [ ] 5.2 **ZIP extract tests** — corpus archives extract cleanly under STRICT; overwrite
      policies; `extract_all(members=[...])` subset in one pass.
- [ ] 5.3 **Seekable TAR extract** — basic extract via default `_iter_with_data()`; the
      archive-wide ratio uses `compressed_source_size` from `phase-4-tar-streaming` (merged
      first), so a seekable `.tar.gz` exercises that guard here too.

## 6. Adversarial corpus + integration

- [ ] 6.1 **Fixtures** — committed adversarial archives under `tests/fixtures/adversarial/`
      (path traversal, zip bomb) + JSON sidecars if needed; or generate via
      `tests/create_adversarial.py` per `testing-contract`.
- [ ] 6.2 **`testing-contract` scenarios** — *path traversal member*; *zip bomb
      extraction* (per-member + cumulative limits).
- [ ] 6.3 **Non-seekable `tar.gz` extract** — integration test over the forward-only
      `_iter_with_data()` override from `phase-4-tar-streaming` (merged first).
- [ ] 6.4 **Retire** matching frozen-oracle extraction coverage as tests transfer.

## 7. Gates

- [ ] 7.1 `uv run pyrefly check` + `uv run ty check` + `uv run ruff` clean.
- [ ] 7.2 Coordinator is a pull-based sink (no push-model `ExtractionHelper`); bounded maps
      are fine. A `pending_*` grep over `src/archivey/` stays as a light tripwire against
      reintroducing the DEV state sprawl, not a hard architectural ban.
- [ ] 7.3 All new tests green; adversarial scenarios pass.
