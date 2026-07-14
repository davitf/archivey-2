## Why

The seekable decompressor layer is a two-level inheritance tree
(`DecompressorStream` → `SegmentedDecompressorStream` → 8 codec subclasses) with
**two** distinct codec plug-in interfaces (a 4-method one on the base, a 2-method
one on the segmented subclass, the latter implemented in terms of the former).
The split forces a construction-order landmine (cursors pre-declared before
`super().__init__`), near-homonym hooks (`_create_decompressor` vs
`_make_decompressor`), and leaks: `UnixCompressDecompressorStream` re-overrides the
base's feed/flush plumbing anyway. Same guarantees are reachable with one concrete
stream and one codec interface.

## What Changes

- Collapse the tree into **one concrete `DecompressorStream`** that holds a
  `Decoder` strategy object instead of being subclassed per codec.
- Unify on the **single `feed/flush/is_finished → (bytes, segments)` protocol**
  already used by the segmented decoders; forward-only codecs return empty
  segments. Removes the second (4-method) interface.
- **Fold index-building into the `Decoder`** as a method with a default no-op stub,
  so forward-only decoders stay trivial and xz/lzip override just that one method.
- Move segment→seek-point bookkeeping and the compressed/decompressed cursors
  **into the base** (identical across xz/lzip today), deleting the construction
  landmine.
- **Delete `SegmentedDecompressorStream` as a class.** The five forward-only
  codecs become five small explicit `Decoder` adapters (no config-driven merge);
  xz/lzip/unix-compress decoders keep their existing parsers verbatim.
- **BREAKING (internal only):** the `DecompressorStream[T]` / `SegmentedDecompressorStream`
  subclass API is removed. No public API changes — `open_stream`, `ArchiveStream`,
  and codec dispatch in `codecs.py` keep their signatures.

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `compressed-streams` — the "Read-only stream wrappers share one internal base"
  requirement gains the composition contract (single stream + one `Decoder`
  protocol; adding a codec adds no stream subclass). Behavior/guarantees unchanged.

## Impact

- Modules: `internal/streams/decompressor_stream.py` (rewritten, smaller),
  `decompress.py` (5 adapters), `xz.py` / `lzip.py` / `unix_compress.py` (decoders
  keep parsers, drop subclass shells), construction sites in `codecs.py`.
- Public API: none. Extras/deps: none.
- Tests: existing `test_seekable_streams.py` / `test_stream_inputs.py` are the
  behavior oracle and must stay green in `[all]` / `[all-lowest]` / `[core-only]`;
  no new required behavior.
