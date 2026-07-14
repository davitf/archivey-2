## 1. Decoder protocol + base stream

- [ ] 1.1 Define `Segment`, the `Decoder` `Protocol`, and a `BaseDecoder` with a no-op `build_index` in `decompressor_stream.py` (keep `SeekPoint`).
- [ ] 1.2 Rewrite `DecompressorStream` as one concrete, non-generic class taking `make_decoder: Callable[[SeekPoint], Decoder]`; move `_comp_cursor`/`_decomp_cursor` and the segment→seek-point bookkeeping into its `feed`/`flush` path.
- [ ] 1.3 Drive index-building through `decoder.build_index` inside `_ensure_index_built`; keep `_index_enabled`, O(n) seek-from-origin, and inner-position restore intact.
- [ ] 1.4 Delete `SegmentedDecompressorStream` and INTERFACE #1 abstracts (`_create_decompressor`, `_decompress_chunk`, `_flush_decompressor`, `_is_decompressor_finished`); extract `_build_index_backwards` as a shared free function.

## 2. Port codecs to decoders

- [ ] 2.1 `decompress.py`: turn the five forward-only streams into five explicit `BaseDecoder` adapters (`ZlibDecoder`, `BrotliDecoder`, `PpmdDecoder`, `BcjDecoder`, `Deflate64Decoder`), preserving each quirk (zlib flush tail, ppmd trailing-NUL, bcj `unpack_size`).
- [ ] 2.2 `xz.py`: keep `_XzState`/`_XzBlockChain`/parsers; expose an XZ decoder factory that reproduces the `point.state` dual-decoder selection and overrides `build_index`.
- [ ] 2.3 `lzip.py`: keep `_LzipState`/trailer scan; expose a lzip decoder overriding `build_index` via the shared backward-scan helper.
- [ ] 2.4 `unix_compress.py`: fold header-commit + pending-truncation into the LZW decoder/its factory (surfaced through the segment channel); preserve CLEAR seek points and the `.Z` truncation rules with no base overrides.
- [ ] 2.5 `codecs.py`: update construction sites to build `DecompressorStream(src, make_decoder=…)` via thin per-codec factories; leave dispatch, availability gates, and exception translation unchanged.

## 3. Verify

- [ ] 3.1 `uv run pytest tests/test_seekable_streams.py tests/test_stream_inputs.py tests/test_binaryio.py` — the behavior oracle — green.
- [ ] 3.2 Full suite green in all three dep configs (`[all]`, `[all-lowest]`, `[core-only]`) per CONTRIBUTING "Before pushing".
- [ ] 3.3 `uv run pyrefly check` and `uv run ty check` clean; no lingering references to `SegmentedDecompressorStream` or the removed abstracts.
- [ ] 3.4 `openspec validate --strict decompressor-stream-composition`.
