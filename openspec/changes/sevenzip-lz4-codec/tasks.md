## 1. Implement

- [ ] 1.1 Wire 7z method `0x04f71104` → shared `Codec.LZ4` (`_METHOD_LZ4`, algorithm table, `_SINGLE_STAGE_CODECS`)
- [ ] 1.2 Add `lz4` to the `[7z]` extra in `pyproject.toml` (standalone `[lz4]` unchanged; also covers 7z folders)
- [ ] 1.3 Tests: LZ4 7z round-trip / py7zr `lz4.7z` corpus; missing-`lz4` raises `PackageNotInstalledError`

## 2. Verify

- [ ] 2.1 `tests/test_sevenzip_reader.py` + `ARCHIVEY_PY7ZR_TEST_FILES=… pytest tests/test_py7zr_corpus.py -k lz4`
- [ ] 2.2 `openspec validate --strict sevenzip-lz4-codec`
