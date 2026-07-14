## Context

The seekable decompressor layer lives in
`internal/streams/decompressor_stream.py` and its codec backends
(`decompress.py`, `xz.py`, `lzip.py`, `unix_compress.py`). Current shape:

```
ReadOnlyIOStream
  └─ DecompressorStream[DecompressorT]         (seek engine + INTERFACE #1)
       ├─ Zlib / Brotli / Ppmd / Bcj / Deflate64   (forward-only leaves)
       └─ SegmentedDecompressorStream[_SDT]     (reifies #1 via INTERFACE #2)
            ├─ XzDecompressorStream
            ├─ LzipDecompressorStream
            └─ UnixCompressDecompressorStream
```

- **INTERFACE #1** (base abstract): `_create_decompressor(point)`,
  `_decompress_chunk(chunk)`, `_flush_decompressor()`, `_is_decompressor_finished()`.
- **INTERFACE #2** (segmented abstract): `_make_decompressor(point)`,
  `_on_completed_segments(units)` over a `_SegmentDecompressor` protocol
  (`feed/flush → (bytes, list[(dec,comp)])`, `is_finished`).
  `SegmentedDecompressorStream` exists mostly to express #1 in terms of #2.

Constraints that make this load-bearing (from `seekable-decompressor-streams`
and `compressed-streams` specs — all must survive byte-for-byte):
demand-driven index (`_index_enabled`); O(n) seek-from-origin even with the index
off (compressed TAR); `_ensure_index_built` restoring inner position; the dual XZ
decoder (`_XzState` sequential vs `_XzBlockChain` post-index); `SEEK_INDEX_DEGRADED`
/ `STREAM_REWIND_REDECOMPRESSES` diagnostics; truncation/digest at clean EOF; the
unix-compress CLEAR-seekpoint + pending-truncation rules.

The accelerator lineage (`_AcceleratorStream`, `_GzipTruncationCheckStream`,
rapidgzip) descends from `DelegatingStream`, **not** `DecompressorStream`, and is
out of scope.

`review/complexity.md` (external review, PR #73) blessed
`SegmentedDecompressorStream` as "correct abstraction — don't touch." The
maintainer has reviewed that note in light of the leaks below and judged it stale;
it does not constrain this change.

## Goals / Non-Goals

**Goals:**
- One concrete `DecompressorStream`; codecs are `Decoder` strategy objects, not
  subclasses.
- Exactly one codec plug-in interface (the `feed/flush/is_finished → (bytes,
  segments)` protocol), with index-building folded in as a defaulted method.
- Delete `SegmentedDecompressorStream` as a class and the construction-order
  landmine.
- Preserve every behavioral guarantee; the existing stream tests are the oracle.

**Non-Goals:**
- No change to the accelerator/rapidgzip path, `codecs.py` dispatch semantics,
  `ArchiveStream`, `verify.py`, or any public API.
- No merge of the five forward-only adapters into one config-driven adapter
  (explicitly rejected below).
- No new random-access capability for codecs that lack one today.

## Investigations

**Where the confusion concentrates (evidence for the collapse):**

| Symptom | Location | Root cause |
| --- | --- | --- |
| Two plug-in interfaces to learn per segmented codec | base #1 + segmented #2 | #2 is translated back into #1 by the middle class |
| Construction-order landmine (cursors pre-set before `super().__init__`) | `decompressor_stream.py:323`, `unix_compress.py:308-311` | base calls `_create_decompressor` inside `__init__` while cursors live in the subclass |
| Near-homonym hooks | `_create_decompressor` vs `_make_decompressor` | one wraps the other |
| "Shared" segmented base doesn't actually shield the leaf | `unix_compress.py:322-352` re-overrides `_decompress_chunk`, `_flush_decompressor`, `read`, `_reset_to_seek_point` | header-commit + pending-truncation have nowhere else to live |

**Interface unification is free:** the segmented `_SegmentDecompressor` protocol
(`feed/flush → (bytes, units)`, `is_finished`) already subsumes the forward-only
4-method interface — a forward-only decoder is just one that always returns
`units == []` and whose `flush()` emits any tail. So #1 is redundant, not #2.

**Cursor/bookkeeping is identical across xz and lzip** (`_on_completed_segments`
loops the same `_comp_cursor`/`_decomp_cursor` += pattern; unix-compress adds a
header commit). That logic is base-worthy once the decoder owns nothing but decode.

## Decisions

### 1. One concrete `DecompressorStream` parameterized by a `Decoder`

`DecompressorStream` stops being generic/abstract. It takes a decoder **factory**
`make_decoder: Callable[[SeekPoint], Decoder]` at construction. The base owns the
buffer, `_pos`/`_size`, the seek-point table, the seek algorithm, the cursors, and
the demand-driven index orchestration. It calls `make_decoder(point)` on init and
on every `_reset_to_seek_point`.

**Rejected:** keeping `DecompressorStream` abstract with a single
`_make_decoder` hook. That preserves an inheritance step for no gain — a factory
callable removes the subclass entirely and lets the base set cursors from `point`
before any decoder exists, deleting the landmine.

### 2. One `Decoder` protocol, index folded in with a default no-op

```python
class Decoder(Protocol):
    def feed(self, data: bytes) -> tuple[bytes, list[Segment]]: ...
    def flush(self) -> tuple[bytes, list[Segment]]: ...
    def is_finished(self) -> bool: ...
    def build_index(self, inner: BinaryIO, last_known: SeekPoint
                    ) -> tuple[list[SeekPoint], int | None]: ...
```

`Segment = tuple[int, int]` (decompressed_size, compressed_size), as today. A
small `BaseDecoder` supplies `build_index → ([], None)` so forward-only adapters
inherit the no-op and implement only feed/flush/is_finished. xz/lzip override
`build_index`; the shared backward-scan helper (`_build_index_backwards`) becomes a
free function they call.

**Rejected:** a separate `Indexer` strategy object. Per maintainer preference,
keeping index-building on the decoder means "everything about a codec" is one
object, and the default stub keeps forward-only decoders trivial.

### 3. Base owns cursors + segment→seek-point bookkeeping

`_comp_cursor`/`_decomp_cursor` and the `add_seek_points` loop move into the base's
feed path: after each `feed`/`flush`, the base consumes the returned `segments` and
advances cursors + registers seek points. The decoder returns *what completed*; the
base decides *what that means for seeking*. unix-compress's header commit (which
shifts the origin seek point past the 3-byte header and sets cursors to
`_HEADER_SIZE`) is expressed as a first-segment signal the base already handles, or
kept inside `LzwState` and surfaced via the same segment channel — chosen during
apply to keep the CLEAR-seekpoint spec scenarios green.

### 4. Five explicit forward-only adapters

`ZlibDecoder`, `BrotliDecoder`, `PpmdDecoder`, `BcjDecoder`, `Deflate64Decoder` —
each a small `BaseDecoder` subclass wrapping its library object, with the same
quirks they carry today (ppmd trailing-NUL, bcj `unpack_size` tracking, zlib
`flush()` tail). They are decoders now, not streams.

**Rejected:** collapsing zlib/brotli/deflate64 into one config-driven adapter.
Saves ~20 lines but bcj/ppmd can't join it, so the win is partial and the
indirection (method-name/flush-policy config) hurts readability more than three
near-identical 6-line classes do.

### 5. XZ dual decoder stays inside the XZ `make_decoder`

The `_XzState` (sequential) vs `_XzBlockChain` (post-index, chosen on
`point.state`) selection currently in `_make_decompressor` moves verbatim into the
XZ decoder factory closure — same inputs (`point`, the seek-point table), same
output. No base awareness of the duality.

### 6. `codecs.py` construction sites call thin factories

Where `codecs.py` builds `XzDecompressorStream(src, …)` today, it builds
`DecompressorStream(src, make_decoder=_xz_decoder_factory(...), …)` (or a one-line
`open_xz(...)` helper beside each decoder). Dispatch semantics, availability gates,
and exception translation are unchanged.

## Risks / Trade-offs

- **[Behavioral regression in a subtle seek/truncation path]** → The change is
  pure restructuring; `test_seekable_streams.py`, `test_stream_inputs.py`, and
  `streams_util.py` are the oracle. Run all three dep configs (`[all]`,
  `[all-lowest]`, `[core-only]`) before pushing, per CONTRIBUTING.
- **[unix-compress header-commit is the trickiest port]** (it mutates the origin
  seek point and gates cursor init on header params) → land it behind the existing
  `.Z` seek/truncation scenarios; decide method-3 placement empirically against
  those tests rather than up front.
- **[`Decoder` protocol widened by `build_index` may tempt future misuse]** →
  `BaseDecoder` default keeps it invisible to forward-only codecs; only index-
  bearing codecs override it.

## Open Questions

- None blocking. The one implementation choice left open (unix-compress header
  commit as a base-visible first segment vs decoder-internal state) is settled
  during apply against the `.Z` spec scenarios.
