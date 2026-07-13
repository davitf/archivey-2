## 1. Data model + codec descriptor

- [x] 1.1 Add `StreamFormat.LZMA_ALONE = "lzma"` and `ArchiveFormat.LZMA_ALONE` (RAW_STREAM × LZMA_ALONE); keep raw 7z/ZIP `Codec.LZMA` (`FORMAT_RAW`) separate from a new Alone stream codec (`Codec.LZMA_ALONE` / `LzmaAloneCodec`)
- [x] 1.2 Register Alone as a standalone `StreamCodec`: `FORMAT_ALONE` open, size-from-header metadata, extensions (`.lzma`), content probe (bounded Alone decode; no exact magic)
- [x] 1.3 Wire Alone into `_BY_STREAM_FORMAT` / single-file `FORMATS` so detection and `SingleFileBackend` pick it up with no new backend class

## 2. Detection + TAR aliases

- [x] 2.1 Keep TAR short alias `.tlz` as TAR×LZIP (with `.lz` / `.tar.lz`); do not remap it to Alone — Alone uses `.lzma` / `.tar.lzma`
- [x] 2.2 Ensure Alone participates in inner-TAR probing (same bounded sequential path as other stream codecs)
- [x] 2.3 Confirm Alone `.tlz` content still detects as TAR×LZMA_ALONE (content wins) and surfaces `FORMAT_EXTENSION_CONFLICT` against the lzip alias; lzip `.tlz` stays TAR×LZIP with no Alone claim

## 3. Tests

- [x] 3.1 Detection/open round-trip for bare `.lzma` Alone and TAR×Alone (`.tar.lzma`)
- [x] 3.2 `.tlz` matrix: lzip fixtures stay TAR×LZIP; Alone fixtures detect as TAR×LZMA_ALONE with extension conflict and read members
- [x] 3.3 Drop or convert the libarchive corpus xfails for `test_compat_lzma_{1,2,3}.tlz` once they pass (N/A on this branch: main corpus triage is 7z-only; Alone `.tlz` covered by unit tests)
- [x] 3.4 Member naming/size: strip `.lzma`; header size when known, `None` for unknown-size marker

## 4. Verify

- [x] 4.1 `uv run --no-sync pytest` on detection / codecs / single-file / libarchive corpus filters for the Alone fixtures
- [x] 4.2 `uv run --no-sync ruff check` / `ruff format --check` and `pyrefly check` / `ty check` on touched files
- [x] 4.3 `openspec validate --strict support-lzma-alone`
