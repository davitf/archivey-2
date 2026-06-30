# Tasks — Phase 4b: Safe extraction (`ExtractionCoordinator` + bomb limits)

> Run tools through uv: `uv run pytest`, `uv run pyrefly check`, `uv run ty check`,
> `uv run ruff`.
> Prerequisites: Phase 3 complete (ZIP backend is the first extract vertical slice) **and
> `phase-4-tar-streaming` merged first** (provides `compressed_source_size` and the
> forward-only `_iter_with_data()` override this change consumes).
> Clean-slate: write `ExtractionCoordinator` fresh — do **not** port DEV's
> `ExtractionHelper` / `pending_*` state machine.
> Module paths below are post-`package-layout-restructure`: public API at
> `src/archivey/` root, internals under `src/archivey/internal/`.

> **DEV source map** (reference only, pin `730275b…`): `internal/extraction.py` /
> `filters.py` in DEV for behavioral edge cases to re-derive tests from — not for
> copy-paste.

## 0. Decisions locked in this change (no code, just honored below)

- [ ] 0.1 **Single forward pass** over `_iter_with_data()` `(member, stream)` pairs —
      coordinator algorithm follows `safe-extraction` spec, not ARCHITECTURE §2.6's old
      `open_fn` sketch.
- [ ] 0.2 **Archive-wide bomb ratio** when `compressed_source_size` is known (spec delta);
      per-member ratio when `member.compressed_size` is known (ZIP).
- [ ] 0.3 **Transforms on transient copy**; `BombTracker` + `ExtractionResult` use
      **original** member.
- [ ] 0.4 **No `pending_*` attributes anywhere** in extraction code (PLAN gate).
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

- [ ] 3.1 **`src/archivey/internal/extraction.py`** — `ExtractionCoordinator.run(reader, dest, …)` drives
      one pass via `reader._iter_with_data()` (respect `members` selector + user `filter` on
      transient copy).
- [ ] 3.2 **FILE extraction** — chunked copy with `BombTracker.count()`; apply mode/mtime
      best-effort after write; honor `OverwritePolicy`.
- [ ] 3.3 **DIR extraction** — `mkdir` with policy mode.
- [ ] 3.4 **SYMLINK extraction** — `os.symlink` + post-creation resolve check with
      `ELOOP`/`RuntimeError` guard (per spec pseudocode).
- [ ] 3.5 **`OnError.STOP` vs `CONTINUE`** — partial file cleanup; `ExtractionResult` with
      `FAILED`/`REJECTED` + `error`; cumulative bomb always stops.
- [ ] 3.6 **`on_progress` callback** — once per member with counters from spec.
- [ ] 3.7 **Wire `ArchiveReader.extract_all()`** — replace `NotImplementedError`; return
      `list[ExtractionResult]`.

## 4. Hardlinks (source always precedes its links in TAR order; see `format-tar` MODIFIED delta)

- [ ] 4.1 **Streaming mode** — `os.link` (or `copy2` on cross-device) when the source was
      already extracted; explicit `ExtractionError` when the source was filtered out (a
      forward pass cannot recover its bytes). No deferred post-pass.
- [ ] 4.2 **Random-access mode (forward-staging, no seek-back / no re-decompression)** —
      pre-pass builds the hardlink closure map from member metadata (TAR `linkname` is in the
      header — no payload reads). When the forward pass reaches an excluded-but-needed source,
      write its content to the first selected link's path *then* (or a `dest/.archivey-tmp-<id>`
      temp), and `os.link` further selected links when reached. Never create the excluded
      source at its own path. State is a bounded `{source → link path}` map drained during the
      pass — no `pending_*` deferred-creation machine, no second decompression pass.
- [ ] 4.3 **Tests** — hardlink to prior member; cross-device fallback (mock `os.link`);
      excluded-source random-access scenario and streaming filtered-source error from
      `safe-extraction` / `format-tar` specs; **solid `.tar.gz` excluded-source resolves with
      a single decompression pass** (assert no re-decompression / no second pass).

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
- [ ] 7.2 No `pending_*` attributes in `src/archivey/` (grep gate).
- [ ] 7.3 All new tests green; adversarial scenarios pass.
