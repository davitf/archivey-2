# Tasks — gzip truncation backstop for any seekable source

> Investigation + specs + implementation. Read `design.md` first (up-front ISIZE capture,
> non-owning wrapper reuse, Bug-3 trap shim + open decision). Run tooling through `uv`
> (`uv run pytest`, `uv run pyrefly check`, `uv run ty check`, `uv run ruff`). Test in all three
> dependency configs before pushing.

## 1. Confirm the mechanism generalizes

- [ ] 1.1 Verify `_gzip_isize_from_source` returns the trailer for a seekable `BinaryIO`
      (`BytesIO`, caller file object) with position restored; confirm `_config_with_gzip_isize`
      currently discards the value.
- [ ] 1.2 Confirm `_begin_stdlib_fallback` only runs after `old.close()` (no live rapidgzip when
      the fallback re-decodes), so a source rewind cannot race the accelerator.

## 2. Source lifetime (Obstacle 1)

- [ ] 2.1 Feed rapidgzip a non-owning view (`ensure_bufferedio` / `BinaryIOWrapper`) of a
      caller-owned source so `old.close()` leaves it open.
- [ ] 2.2 Test: caller source is still open/readable after the archivey stream closes (parity
      with `test_ensure_bufferedio_does_not_close_raw_source`).

## 3. Bug-3 boundary (Obstacle 2 — gating investigation)

- [ ] 3.1 Sweep a **raising** file object behind rapidgzip under a wall-clock timeout; record
      whether Bug 3 fires for (a) truncated non-raising bytes vs (b) a source that itself raises.
- [ ] 3.2 If needed, prototype the exception-trapping source shim (trap → store → benign EOF →
      re-raise via `_AcceleratorStream`); confirm no process abort and a clean translated error.
- [ ] 3.3 **Maintainer decision** (design.md "Open decision"): (a) trap-then-enable,
      (b) backstop-now/trap-later, or (c) a speed-vs-robustness config axis. Record the choice
      here before implementing §4.

## 4. Generalize the backstop

- [ ] 4.1 Capture the ISIZE int up front (on `StreamConfig` or `_GzipTruncationCheckStream`);
      drop the EOF path re-open in `_verify_not_truncated`.
- [ ] 4.2 Make `_begin_stdlib_fallback` rewind the seekable source instead of re-opening a path;
      keep the path branch unchanged.
- [ ] 4.3 Remove the `isinstance(source, (str, os.PathLike))` gate in `GzipCodec.open` so the
      check stream wraps any declared-seekable source (subject to §3.3).
- [ ] 4.4 Multi-member disambiguation on a non-path source: seek the same source (save/restore),
      or defer to sibling change `gzip-multimember-detect-via-index` if it lands first.

## 5. Tests + docs

- [ ] 5.1 Truncated single-member gzip from `BytesIO` / file object → `TruncatedError`
      (mirror the path-source cases in `tests/test_accelerator_corruption.py`).
- [ ] 5.2 Empty→stdlib fallback over a rewound source recovers the same prefix as a path source.
- [ ] 5.3 Update `docs/gotchas.md` — remove "non-path sources" from the residual-holes row for
      bare `.gz` once §4 lands.
- [ ] 5.4 `uv run pyrefly check` + `uv run ty check` clean; `uv run ruff format`; full suite in
      `[all]`, `[all-lowest]`, `[core-only]`.

## 6. OpenSpec

- [ ] 6.1 `openspec validate --strict gzip-truncation-backstop-any-seekable` green.
- [ ] 6.2 Sync the delta into main `seekable-decompressor-streams` when landing (coordinate with
      `gzip-multimember-detect-via-index`, which edits the same requirement).
