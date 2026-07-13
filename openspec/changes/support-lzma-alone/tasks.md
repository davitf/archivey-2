## 1. Data model + codec descriptor

- [ ] 1.1 Add `StreamFormat.LZMA = "lzma"` and `ArchiveFormat.LZMA` (RAW_STREAM × LZMA); keep raw 7z/ZIP `Codec.LZMA` (`FORMAT_RAW`) separate from a new Alone stream codec (e.g. `Codec.LZMA_ALONE` / `LzmaAloneCodec`)
- [ ] 1.2 Register Alone as a standalone `StreamCodec`: `FORMAT_ALONE` open, size-from-header metadata, extensions (`.lzma`), content probe (bounded Alone decode; no exact magic)
- [ ] 1.3 Wire Alone into `_BY_STREAM_FORMAT` / single-file `FORMATS` so detection and `SingleFileBackend` pick it up with no new backend class

## 2. Detection + TAR aliases

- [ ] 2.1 Remap TAR short alias `.tlz` from TAR×LZIP to TAR×LZMA Alone; keep `.lz` / `.tar.lz` as lzip
- [ ] 2.2 Ensure Alone participates in inner-TAR probing (same bounded sequential path as other stream codecs)
- [ ] 2.3 Confirm LZIP magic still wins for `test_compat_lzip_1.tlz` and surfaces `FORMAT_EXTENSION_CONFLICT` against the `.tlz` Alone alias

## 3. Tests

- [ ] 3.1 Detection/open round-trip for bare `.lzma` Alone and TAR×Alone (`.tar.lzma` / Alone `.tlz`)
- [ ] 3.2 `.tlz` matrix: Alone fixtures detect as TAR×LZMA and read members; lzip `.tlz` stays TAR×LZIP with extension conflict
- [ ] 3.3 Drop or convert the libarchive corpus xfails for `test_compat_lzma_{1,2,3}.tlz` once they pass
- [ ] 3.4 Member naming/size: strip `.lzma`; header size when known, `None` for unknown-size marker

## 4. Verify

- [ ] 4.1 `uv run --no-sync pytest` on detection / codecs / single-file / libarchive corpus filters for the Alone fixtures
- [ ] 4.2 `uv run --no-sync ruff check` / `ruff format --check` and `pyrefly check` / `ty check` on touched files
- [ ] 4.3 `openspec validate --strict support-lzma-alone`
