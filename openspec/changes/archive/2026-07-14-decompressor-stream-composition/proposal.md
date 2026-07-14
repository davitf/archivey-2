## Why

The seekable decompressor layer is a two-level inheritance tree
(`DecompressorStream` → `SegmentedDecompressorStream` → 8 codec subclasses) with
**two** distinct codec plug-in interfaces (a 4-method one on the base, a 2-method
one on the segmented subclass, the latter implemented in terms of the former).
The split forces a construction-order landmine (cursors pre-declared before
`super().__init__`), near-homonym hooks (`_create_decompressor` vs
`_make_decompressor`), untyped `SeekPoint.state`, and leaks:
`UnixCompressDecompressorStream` re-overrides the base's feed/flush/read plumbing
anyway. The same guarantees are reachable with one concrete stream and one codec
interface. (PR #92 is an independent spike that converged on the same
architecture; this change adopts its sharper concepts — see design.md.)

## What Changes

- Collapse the tree into **one concrete `DecompressorStream`** (name kept) that
  holds a `Decoder` strategy object instead of being subclassed per codec.
- **One `Decoder` protocol** — `recreate`/`feed`/`flush`/`finished` over a
  `DecodeOut(data, points)`, plus a formal `pending_error` property and a folded-in
  `build_index`. Replaces both old interfaces and the `_create_`/`_make_` pair.
- **Fold index discovery into the `Decoder`** (default no-op), and have the decoder
  **emit absolute `SeekPoint`s directly** — so before/after placement and XZ
  progressive enrichment live in the decoder and the base stays format-agnostic.
  No separate `SeekTable`.
- Deferred unix-compress `TruncatedError` moves to `Decoder.pending_error`; XZ's
  `_XzState`/`_XzBlockChain` choice moves inside `recreate`. Both delete leaf-level
  base overrides.
- **Delete `SegmentedDecompressorStream` as a class.** Five small explicit
  forward-only `Decoder` adapters (no config-driven merge); xz/lzip/unix-compress
  keep their parsers verbatim, losing only the stream-subclass tails.
- Coordinate with in-flight `seekable-gzip-and-block-writing`: BGZF plugs in as a
  `Decoder` + forward-member-walk index, **not** a new subclass leaf.
- **BREAKING (internal only):** the `DecompressorStream[T]` /
  `SegmentedDecompressorStream` subclass API is removed. No public API changes —
  `open_stream`, `ArchiveStream`, `try_get_size`, and codec dispatch keep their
  signatures.

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `compressed-streams` — the "Read-only stream wrappers share one internal base"
  requirement gains the composition contract (single stream + one `Decoder`
  protocol that also owns index discovery; adding a codec adds no stream subclass).
  Behavior/guarantees unchanged.

## Impact

- Modules: `internal/streams/decompressor_stream.py` (rewritten, smaller),
  `decompress.py` (5 adapters), `xz.py` / `lzip.py` / `unix_compress.py` (decoders
  keep parsers, drop subclass shells), construction sites in `codecs.py`. The
  `isinstance(stream, DecompressorStream)` gate in `single_file_reader.py:222`
  keeps working (name kept).
- Public API: none. Extras/deps: none.
- Tests: existing `test_seekable_streams.py` / `test_stream_inputs.py` /
  unix-compress `.Z` seek matrix are the behavior oracle and must stay green in
  `[all]` / `[all-lowest]` / `[core-only]`; no new required behavior.
- Related in-flight: `seekable-gzip-and-block-writing` must re-target onto this
  `Decoder` model rather than landing another `DecompressorStream` subclass.
