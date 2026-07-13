## 1. LZW kernel + stream

- [ ] 1.1 Add `LzwState` (`feed`/`flush`/`is_finished`, CLEAR in-memory realignment, relative segment units) adapted from uncompresspy with BSD-3-Clause attribution
- [ ] 1.2 Implement `UnixCompressDecompressorStream(SegmentedDecompressorStream)`: cursors, CLEAR units → `SeekPoint` after advance, EOF-finished (no `TruncatedError`), no trailer `_build_index`
- [ ] 1.3 Point `UnixCompressCodec.open` at the native stream (`seekable=config.seekable`); drop `uncompresspy` import, requirement, and seekable-only error translation

## 2. Packaging + docs

- [ ] 2.1 Remove `[unix-compress]` / `uncompresspy` from `pyproject.toml` extras, recommended-lite, and the `dev` group; keep `ncompress` for fixtures
- [ ] 2.2 Update `docs/internal/library-analysis.md`, purpose prose that still names `uncompresspy`, and remove the IDEAS.md non-seekable `.Z` bullet
- [ ] 2.3 Sync main specs from this change’s deltas (`compressed-streams`, `packaging-and-extras`, `format-single-file-compressors`, `seekable-decompressor-streams`) when applying

## 3. Tests

- [ ] 3.1 Rewrite unix-compress tests: drop `@requires("uncompresspy")` and the missing-backend case; keep `ncompress` for fixture generation
- [ ] 3.2 Cover non-seekable forward decode, seekable CLEAR seek-point seeks (no rewind diagnostic), corruption → `CorruptionError`, truncated short read without `TruncatedError`
- [ ] 3.3 Confirm core-only / extras-import guards: `.Z` works without third-party packages; `uncompresspy` is not a leaf extra

## 4. Verify

- [ ] 4.1 Targeted pytest for unix-compress / codecs / single-file / seek behavior
- [ ] 4.2 `openspec validate --strict vendor-unix-compress-lzw`
