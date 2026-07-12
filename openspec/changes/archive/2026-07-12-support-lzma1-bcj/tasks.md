## 1. Packaging

- [x] 1.1 Add `pybcj>=1.0.6` to the `[7z]` extra in `pyproject.toml` and refresh the lockfile
- [x] 1.2 Map `pybcj` → import name `bcj` in `tests/test_extras_imported.py` so the extras guard passes
- [x] 1.3 Note LZMA1+BCJ / `pybcj` in `docs/library-analysis.md` (and sync main `packaging-and-extras` / `format-7z` specs when applying)

## 2. Decode path

- [x] 2.1 In `_open_lzma_run`, when LZMA1 and BCJ co-occur: require `pybcj`, decode non-BCJ filters via stdlib `lzma`, then wrap each BCJ stage with `bcj.*Decoder(unpack_size)` — never a combined LZMA1+BCJ `FORMAT_RAW` chain
- [x] 2.2 Pass per-coder `unpack_sizes` from the folder into the staged path so BCJ can flush look-ahead
- [x] 2.3 Raise `PackageNotInstalledError` naming `pybcj` / `[7z]` when the staged path is needed and `bcj` is missing

## 3. Tests

- [x] 3.1 Change `test_lzma1_bcj_fixture_is_rejected` to a py7zr round-trip under `@requires("bcj")`
- [x] 3.2 Add a 7-Zip CLI `-m0=BCJ -m1=LZMA` fixture (~12800 bytes) that must round-trip (catches silent liblzma truncation)
- [x] 3.3 Keep BCJ2 rejection coverage unchanged

## 4. Verify

- [x] 4.1 `uv run --no-sync pytest tests/test_sevenzip_reader.py tests/test_extras_imported.py -q`
- [x] 4.2 `uv run --no-sync ruff check` / `ruff format --check` and `pyrefly check` / `ty check` on touched files
- [x] 4.3 `openspec validate --strict support-lzma1-bcj`
