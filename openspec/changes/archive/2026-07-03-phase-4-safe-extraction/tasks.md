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

- [x] 0.1 **Pull-based sink** driving `_iter_with_data()` `(member, stream)` pairs — algorithm
      selected from `get_members_if_available()` and (on an orphan) source re-readability, no
      `SOLID`/`DIRECT` cost lookup; follows `safe-extraction` spec, not
      ARCHITECTURE §2.6's old `open_fn` sketch.
- [x] 0.2 **Archive-wide bomb ratio** when `compressed_source_size` is known (spec delta);
      per-member ratio when `member.compressed_size` is known (ZIP).
- [x] 0.3 **Transforms on transient copy**; `BombTracker` + `ExtractionResult` use
      **original** member.
- [x] 0.4 **Pull-model sink, no push-model state machine** — bounded state (plan, per-source
      path list, orphan list) is fine; do not reintroduce DEV's `pending_*` /
      `can_move_file` / `process_file_extracted` sprawl.
- [x] 0.5 **Bomb limits only on extract paths** — `read()` / `open()` unchanged.

## 1. Types and filters

- [x] 1.1 **Enums** — `ExtractionPolicy`, `OverwritePolicy`, `OnError`, `ExtractionStatus`
      (public exports in `__init__.py`).
- [x] 1.2 **Progress/result types** — `ExtractionProgress`, `ExtractionResult` dataclasses
      (`src/archivey/internal/progress.py` or `src/archivey/internal/types.py` per existing
      conventions).
- [x] 1.3 **`src/archivey/internal/filters.py`** — `check_universal()` (path traversal, absolute paths,
      null bytes, symlink/hardlink escape at planning time, `MemberType.OTHER`);
      `transform_strict` / `transform_standard` / identity for `TRUSTED`;
      `POLICY_TRANSFORMS` dict.
- [x] 1.4 **Filter unit tests** — universal rejections; STRICT strips execute/normalizes;
      STANDARD preserves execute; TRUSTED still rejects `..` paths.

## 2. `BombTracker`

- [x] 2.1 **Implement `BombTracker`** per `safe-extraction` spec — cumulative
      `max_extracted_bytes`, per-member ratio with activation threshold, **archive-wide
      ratio** using the new optional `compressed_source_size` constructor arg (coordinator
      reads `reader.compressed_source_size` and passes it in). Archive-wide ratio activates
      on **cumulative** output (`_total_bytes`), not per-member.
- [x] 2.2 **`max_entries` guard** — cumulative entry counter incremented in `start_member()`;
      raise `ExtractionError` when exceeded; always-stop (like `max_extracted_bytes`, halts
      even under `OnError.CONTINUE`); default `1_048_576`, caller-overridable.
- [x] 2.3 **Tests** — cumulative byte limit; per-member ratio (ZIP fixture); activation
      threshold false-positive guard; archive-wide ratio with known outer size; skip when
      denominators unknown; `max_entries` exceeded (many-tiny-files fixture) halts even under
      `CONTINUE`; entry count independent of byte/ratio guards.

## 3. `ExtractionCoordinator` core

- [x] 3.1 **`src/archivey/internal/extraction.py`** — `ExtractionCoordinator.run(reader, dest, …)`
      as a pull-based sink: call `reader.get_members_if_available()` to decide the optional
      planned-pass optimization (§4), then drive `reader._iter_with_data()` (respect `members`
      selector + user `filter` on transient copy).
- [x] 3.2 **FILE extraction** — chunked copy with `BombTracker.count()`; apply mode/mtime
      best-effort after write; honor `OverwritePolicy`.
- [x] 3.3 **DIR extraction** — `mkdir` with policy mode.
- [x] 3.4 **SYMLINK extraction** — `os.symlink` + post-creation resolve check with
      `ELOOP`/`RuntimeError` guard (per spec pseudocode). Target-independent: a symlink to a
      filtered-out/later/external target is created and may dangle (only the within-`dest`
      escape check applies), no copy. `os.symlink` failure on an unsupported filesystem →
      per-member `OnError` failure; **no** copy-the-target fallback.
- [x] 3.5 **`OnError.STOP` vs `CONTINUE`** — partial file cleanup; `ExtractionResult` with
      `FAILED`/`REJECTED` + `error`; cumulative bomb always stops.
- [x] 3.6 **`on_progress` callback** — once per member with counters from spec.
- [x] 3.7 **Wire `ArchiveReader.extract_all()`** — replace `NotImplementedError`; return
      `list[ExtractionResult]`.

## 4. Hardlinks (source precedes its links in TAR order; algorithm-selected — see `format-tar` MODIFIED delta)

- [x] 4.1 **Core algorithm — sequential pass + conditional second pass** (subsumes the
      no-filter case; no separate (A) implementation). One forward pass records each written
      FILE under a per-source **list of on-disk paths**; a link to an already-written source is
      created by trying `os.link` against those paths in turn. No filter → no orphans → one
      pass, done. If a filter orphans a selected link: re-readable source → collect orphans,
      resolve all in **one** second pass afterwards, only if an orphan exists (re-scan for
      plain; re-decompress ≤ 2× for compressed); forward-only source (can't be re-read) →
      per-member `OnError` failure (STOP raises / CONTINUE records `FAILED`), no recovery.
      Never call `members()` speculatively; no `SOLID`/`DIRECT` cost lookup needed.
- [ ] 4.2 **Optional optimization — planned single pass** — when filtering **and**
      `get_members_if_available()` returns a free list, plan selection + policy + filter up front
      into a `source → selected-link-paths` map and stage each needed (even excluded) source to
      the first selected link's path during the single pass, skipping the second pass. Layered on
      the core; can be deferred without affecting correctness.
      **DEFERRED** — the core algorithm (4.1) handles every case correctly; this is a pure
      optimization the current TAR backend rarely triggers (it exposes no free member list until
      materialized). Left for a follow-up to keep this security-sensitive change scoped.
- [x] 4.3 **Cross-device handling (try-list)** — to create a link, try `os.link` against the
      source's recorded on-disk paths in turn; first success wins. On all-`EXDEV`, `shutil.copy2`
      and append the new path so a later same-device link reuses it (handles `C → A` via
      `os.link(B, C)`; better than `tarfile`, which recopies from the archive per link). `st_dev`
      is an optional optimization to skip doomed attempts, not required.
- [x] 4.4 **Tests** — unfiltered → single pass, no list fetched; filter + plain-tar orphan → one
      re-scan second pass; filter + compressed-tar orphan → one second pass (assert decompressed
      ≤ 2×); filter + no orphan → single pass, no speculative list; forward-only orphan →
      `OnError` STOP/CONTINUE; (optional) filter + free-list orphan → planned single pass, no
      second pass; chained cross-device link reuses the sibling copy (mock devices / `os.link`
      `EXDEV`).
- [x] 4.5 **Symlink tests** — dangling symlink to a filtered-out target created within `dest`
      (no copy, no error); `os.symlink` failure on an unsupported filesystem → `OnError`
      STOP/CONTINUE (no copy-the-target fallback, a deliberate deviation from `tarfile`).

## 5. Public API + ZIP vertical slice

- [x] 5.1 **`archivey.extract()`** — top-level one-shot API per spec (no member selector).
- [x] 5.2 **ZIP extract tests** — corpus archives extract cleanly under STRICT; overwrite
      policies; `extract_all(members=[...])` subset in one pass.
- [x] 5.3 **Seekable TAR extract** — basic extract via default `_iter_with_data()`; the
      archive-wide ratio uses `compressed_source_size` from `phase-4-tar-streaming` (merged
      first), so a seekable `.tar.gz` exercises that guard here too.

## 6. Adversarial corpus + integration

- [x] 6.1 **Fixtures** — committed adversarial archives under `tests/fixtures/adversarial/`
      (path traversal, zip bomb) + JSON sidecars if needed; or generate via
      `tests/create_adversarial.py` per `testing-contract`.
- [x] 6.2 **`testing-contract` scenarios** — *path traversal member*; *zip bomb
      extraction* (per-member + cumulative limits).
- [x] 6.3 **Non-seekable `tar.gz` extract** — integration test over the forward-only
      `_iter_with_data()` override from `phase-4-tar-streaming` (merged first).
- [x] 6.4 **Retire** matching frozen-oracle extraction coverage as tests transfer.

## 7. Gates

- [x] 7.1 `uv run pyrefly check` + `uv run ty check` + `uv run ruff` clean.
- [x] 7.2 Coordinator is a pull-based sink (no push-model `ExtractionHelper`); bounded maps
      are fine. A `pending_*` grep over `src/archivey/` stays as a light tripwire against
      reintroducing the DEV state sprawl, not a hard architectural ban.
- [x] 7.3 All new tests green; adversarial scenarios pass.
