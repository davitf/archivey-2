# Tasks — zstd stdlib backend migration

> Implements the zstd decision recorded in `docs/library-analysis.md` /
> `compression-library-evaluation`. Run tools via `uv`.

## 1. Backend

- [ ] 1.1 Resolve the zstd backend module once: `compression.zstd` if importable (3.14+), else
      `backports.zstd`; expose a sentinel like the other optional codecs in `codecs.py`.
- [ ] 1.2 Rewrite `ZstdCodec.open` to use the resolved backend's `ZstdFile`/`open`; raise
      `PackageNotInstalledError` naming the backend when neither module is importable.
- [ ] 1.3 Delete `_ZstdReopenStream` and the backward-seek reopen special-case; rely on the
      stdlib reader's in-place rewind. Keep the `RewindWarning("zstd")` (still index-less).
- [ ] 1.4 Update `ZstdCodec.translate`: `ZstdError` → `CorruptionError`, `EOFError` →
      `TruncatedError` (drop the `zstandard.ZstdError` branch).

## 2. Packaging

- [ ] 2.1 `[zstd]` → `backports.zstd>=…; python_version < "3.14"` (no runtime pin on 3.14+).
      Point the `[7z]` bundle's zstd dependency at the same backend. Remove `zstandard` from the
      runtime extras.
- [ ] 2.2 Confirm `tests/test_extras_imported.py` still passes (the new backend's module is
      imported by `src/`).

## 3. Tests

- [ ] 3.1 Update zstd fixtures/tests that import `zstandard` to the new backend (or a
      backend-agnostic writer).
- [ ] 3.2 Add a truncated-`.zst` test asserting `TruncatedError` (newly possible).
- [ ] 3.3 Update the rewind test to expect the ordinary index-less rewind path (no reopen).

## 4. Specs + verify

- [ ] 4.1 Sync the `compressed-streams`, `packaging-and-extras`, and
      `seekable-decompressor-streams` deltas.
- [ ] 4.2 `uv run pytest` / `pyrefly` / `ty` / `ruff` green.
