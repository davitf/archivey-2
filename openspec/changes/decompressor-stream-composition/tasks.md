## 1. Decoder protocol + base stream

- [ ] 1.1 Define `DecodeOut`, the `Decoder` `Protocol` (`recreate`/`feed`/`flush`/`finished`/`pending_error`/`build_index`), and a `BaseDecoder` with empty `points`, `pending_error = None`, and no-op `build_index` in `decompressor_stream.py` (keep `SeekPoint`, keep the class name `DecompressorStream`).
- [ ] 1.2 Rewrite `DecompressorStream` as one concrete, non-generic, **format-agnostic** class holding a decoder built via `recreate(point, inner)`; it stores whatever `SeekPoint`s `feed`/`flush` emit and drops all per-format cursor/placement branching.
- [ ] 1.3 Wire `pending_error`: base raises and clears it on the next empty `read` after delivering bytes. Preserve SEEK_END / scan-to-EOF / size semantics.
- [ ] 1.4 Drive one-shot discovery through `decoder.build_index` inside `_ensure_index_built` (save/restore `inner`, keep `_index_enabled`, O(n) seek-from-origin, and `SEEK_INDEX_DEGRADED` fallback via the shared `_build_index_backwards` free function).
- [ ] 1.5 Delete `SegmentedDecompressorStream` and INTERFACE #1 abstracts (`_create_decompressor`, `_decompress_chunk`, `_flush_decompressor`, `_is_decompressor_finished`).

## 2. Port codecs to decoders

- [ ] 2.1 `decompress.py`: five explicit `BaseDecoder` adapters (`ZlibDecoder`, `BrotliDecoder`, `PpmdDecoder`, `BcjDecoder`, `Deflate64Decoder`), preserving each quirk (zlib flush tail, ppmd trailing-NUL, bcj `unpack_size`).
- [ ] 2.2 `xz.py`: keep `_XzState`/`_XzBlockChain`/parsers; XZ decoder `recreate` selects the sub-decoder on `point.state`, retains `inner`, and emits stream-start + progressive block-enrichment `SeekPoint`s from `feed`; `build_index` = backward footer/index scan.
- [ ] 2.3 `lzip.py`: keep `_LzipState`/trailer scan; lzip decoder emits member-start points (before-placement) from `feed`; `build_index` = backward trailer scan via the shared helper.
- [ ] 2.4 `unix_compress.py`: keep `LzwState`; decoder emits CLEAR points (after-placement), shifts the origin point past the 3-byte header itself, and sets `pending_error` for leftover-bit truncation — no base overrides.
- [ ] 2.5 `codecs.py`: update construction sites to build `DecompressorStream(src, make_decoder=…)` via thin per-codec factories; leave dispatch, availability gates, and exception translation unchanged.
- [ ] 2.6 Confirm `single_file_reader.py:222` `isinstance(stream, DecompressorStream)` → `try_get_size()` still resolves (name kept; no logic change).

## 3. Verify

- [ ] 3.1 `uv run pytest tests/test_seekable_streams.py tests/test_stream_inputs.py tests/test_binaryio.py` — the behavior oracle — green (incl. the unix-compress `.Z` seek/truncation matrix).
- [ ] 3.2 Full suite green in all three dep configs (`[all]`, `[all-lowest]`, `[core-only]`) per CONTRIBUTING "Before pushing".
- [ ] 3.3 `uv run pyrefly check` and `uv run ty check` clean; no lingering references to `SegmentedDecompressorStream` or the removed abstracts.
- [ ] 3.4 `openspec validate --strict decompressor-stream-composition`.
