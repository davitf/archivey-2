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
out of scope (foreign `BinaryIO`; `weakref.finalize` lifecycle stays at the
object's birth site in `codecs.py`).

`review/complexity.md` (external review, PR #73) blessed
`SegmentedDecompressorStream` as "correct abstraction — don't touch." The
maintainer has reviewed that note in light of the leaks below and judged it stale;
it does not constrain this change.

**Prior art incorporated:** PR #92 (`spike-indexed-decompress-stream`) is an
independent spike on the same problem that converged on the same architecture
(one stream + `Decoder`; delete `SegmentedDecompressorStream`; keep parsers;
accelerators out). This design adopts its sharper concepts — `pending_error`,
the before/after placement policy, the three-index-path enumeration, `DecodeOut`,
and the BGZF coordination — while keeping index discovery **folded into the
`Decoder`** (a maintainer decision that dissolves PR #92's still-open
SeekTable-placement question; see Decision 3).

**Related in-flight:** `seekable-gzip-and-block-writing` plans another
`DecompressorStream` subclass leaf (BGZF). It MUST NOT land that leaf in parallel;
it re-targets onto this composition model as a `Decoder` (Decision 7).

## Goals / Non-Goals

**Goals:**
- One concrete `DecompressorStream` (name kept); codecs are `Decoder` strategy
  objects, not subclasses.
- Exactly one codec plug-in interface — `feed`/`flush`/`finished`/`recreate` over
  a `DecodeOut`, with index discovery folded in and a defaulted no-op for
  forward-only codecs.
- Delete `SegmentedDecompressorStream` as a class and the construction-order
  landmine; keep the base format-agnostic.
- Preserve every behavioral guarantee; the existing stream tests are the oracle.
- Leave a clean plug shape for BGZF / future native zstd frame index.

**Non-Goals:**
- No change to the accelerator/rapidgzip path, `codecs.py` dispatch semantics,
  `ArchiveStream`, `verify.py`, or any public API.
- No merge of the five forward-only adapters into one config-driven adapter
  (rejected below).
- No new random-access capability for codecs that lack one today.
- Implementing BGZF itself (coordinate only).

## Investigations

**Where the confusion concentrates (evidence for the collapse):**

| Symptom | Location | Root cause |
| --- | --- | --- |
| Two plug-in interfaces to learn per segmented codec | base #1 + segmented #2 | #2 is translated back into #1 by the middle class |
| Construction-order landmine (cursors pre-set before `super().__init__`) | `decompressor_stream.py:323`, `unix_compress.py:308-311` | base calls `_create_decompressor` inside `__init__` while cursors live in the subclass |
| Near-homonym hooks | `_create_decompressor` vs `_make_decompressor` | one wraps the other |
| "Shared" segmented base doesn't shield the leaf | `unix_compress.py:322-352` re-overrides `_decompress_chunk`, `_flush_decompressor`, `read`, `_reset_to_seek_point` | header-commit + deferred-truncation have nowhere else to live |
| Untyped resume state | `SeekPoint.state: Any` (`None` → `_XzState`, block bounds → `_XzBlockChain`) | resume strategy stuffed into the decoder *type* instead of a `recreate(point)` choice |

**Three index-building paths exist today** (from PR #92's spike — all must survive):

| Path | Trigger | Today |
| --- | --- | --- |
| Progressive boundary | during `feed`, on completed segment | `_on_completed_segments` → `add_seek_points` |
| Progressive enrichment (XZ only) | during `feed`, on completed stream | `_update_index` seeks `_inner`, scans that stream's footer, restores pos |
| One-shot | `SEEK_END` / seek past known frontier | `_build_index` (backward trailer/footer scan) |

A future forward member walk (BGZF/mgzip via `BC`/`MZ`; zstd seekable footer) is a
fourth shape of the one-shot path. The discovery surface must admit all four.

**Seek-point placement disagrees per format** (folklore today; named here):

| Format | On completed unit | Meaning |
| --- | --- | --- |
| lzip / xz stream boundary | point **before** advancing cursors | resume *at* member/stream start |
| unix-compress CLEAR | advance cursors, **then** point | resume *after* the CLEAR realignment |

**Interface unification is free:** the segmented `_SegmentDecompressor` protocol
already subsumes the forward-only 4-method interface — a forward-only decoder is
one whose output carries no seek points and whose `flush()` emits any tail. So
INTERFACE #1 is the redundant one.

## Decisions

### 1. One concrete `DecompressorStream` parameterized by a `Decoder`

`DecompressorStream` stops being generic/abstract and **keeps its name** (it is the
external vocabulary — specs, docs, and `single_file_reader.py:222`'s
`isinstance(stream, DecompressorStream)` gate around `try_get_size()`). It holds a
`Decoder`, created via `recreate(point, inner)` on init and on every
`_reset_to_seek_point`. The base owns the buffer, `_pos`/`_size`, the seek-point
table, the seek algorithm, and the demand-driven index orchestration — and nothing
format-specific.

**Rejected:** rename to `IndexedDecompressStream` (leaks structure; churns the
isinstance site and specs for no clarity). **Rejected:** keep `DecompressorStream`
abstract with a single `_make_decoder` hook (preserves an inheritance step; a
`recreate` factory removes the subclass and lets the base build the decoder from
`point` with no pre-existing subclass state — deleting the landmine).

### 2. One `Decoder` protocol; `DecodeOut`; `recreate`; `pending_error`

```python
@dataclass
class DecodeOut:
    data: bytes
    points: list[SeekPoint] = field(default_factory=list)  # absolute; usually empty

class Decoder(Protocol):
    def recreate(self, point: SeekPoint, inner: BinaryIO) -> Decoder: ...
    def feed(self, chunk: bytes) -> DecodeOut: ...
    def flush(self) -> DecodeOut: ...
    @property
    def finished(self) -> bool: ...
    @property
    def pending_error(self) -> BaseException | None: ...
    def clear_pending_error(self) -> None: ...
    def build_index(
        self, inner: BinaryIO, last_known: SeekPoint
    ) -> tuple[list[SeekPoint], int | None]: ...
```

A small `BaseDecoder` supplies `points=[]`, `pending_error = None`,
`clear_pending_error`, and a no-op `build_index → ([], None)`. Forward-only adapters
(zlib/brotli/ppmd/bcj/deflate64) implement only `recreate`/`feed`/`flush`/`finished`
and inherit the rest.

- **`pending_error`** (from PR #92, adopted) is a real Protocol property, not
  duck-typing. unix-compress sets it to `TruncatedError` after `flush` when leftover
  bits are nonzero; the base raises and clears it via `clear_pending_error` on the
  next empty `read` after delivering bytes (and on seek reset). This deletes
  unix-compress's `read`/`_reset_to_seek_point` overrides.
- **`recreate`** replaces both `_create_decompressor` and `_make_decompressor`. XZ's
  `_XzState`-vs-`_XzBlockChain` choice lives inside XZ's `recreate` (keyed on
  `point.state`), not a union on the stream type.

**Rejected:** flat `Segment = (int, int)` return (PR #95's first draft) — too narrow
for XZ enrichment points, which carry block-bounds `state`. **Rejected:**
duck-typed `truncated` attribute for `.Z`.

### 3. Index discovery is folded into the `Decoder`, and the Decoder emits points

Index building stays **on the `Decoder`** (defaulted no-op), not a separate
`SeekTable`. Consequently the `Decoder` — which knows its start offset from
`recreate(point, …)` and already tracks byte deltas (`member_size`, `_bytes_fed`) —
**emits absolute `SeekPoint`s directly** in `DecodeOut.points`. The base just stores
them. This covers all three progressive paths without base-side format branching:

- **Progressive boundary:** the decoder puts a point at each member/stream start,
  choosing before/after placement itself (Investigation table) — the base never
  encodes the asymmetry.
- **Progressive enrichment (XZ):** the XZ decoder retains `inner` from `recreate`
  (as `_XzBlockChain` already does), scans the just-completed stream's footer, and
  returns block points in `DecodeOut.points`; it restores `inner`'s position itself.
- **One-shot / forward walk:** `build_index(inner, last_known)` handles `SEEK_END`
  and forward member walks (BGZF); the base still saves/restores `inner` around it
  and keeps the `SEEK_INDEX_DEGRADED` fallback via the shared `_build_index_backwards`
  free function.

This moves the compressed/decompressed cursor bookkeeping **out of the base and into
the three indexed decoders** (net-neutral on lines — they already track those
deltas) and dissolves PR #92's Open Question B: with discovery on the decoder there
is no SeekTable, so "does the table own format I/O or do formats push into it" never
arises. The base is fully format-agnostic.

**Rejected:** a separate `SeekTable` strategy (PR #92 Model 1/2). It re-splits
"store" from "discover" and forces the unresolved placement question; folding into
the decoder keeps "everything about a codec" in one object per the maintainer's
thread-2 decision. **Rejected:** decoder returns relative units, base converts to
absolute points (keeps before/after folklore in the base and can't carry enrichment
`state` cleanly).

### 4. Five explicit forward-only adapters

`ZlibDecoder`, `BrotliDecoder`, `PpmdDecoder`, `BcjDecoder`, `Deflate64Decoder` —
each a small `BaseDecoder` subclass wrapping its library object, preserving each
quirk (zlib `flush()` tail, ppmd trailing-NUL, bcj `unpack_size` tracking). They are
decoders now, not streams.

**Rejected:** collapsing zlib/brotli/deflate64 into one config-driven adapter. Saves
~20 lines but bcj/ppmd can't join it, so the win is partial and the method-name/flush
config hurts readability more than three near-identical 6-line classes do.

### 5. `codecs.py` construction sites call thin factories

Where `codecs.py` builds `XzDecompressorStream(src, …)` today it builds
`DecompressorStream(src, make_decoder=_xz_decoder(...), …)` (or a one-line
`open_xz(...)` helper beside each decoder). Dispatch semantics, availability gates,
and exception translation are unchanged.

### 6. Migration touches the `isinstance` gate, not the contract

`single_file_reader.py:222` keeps `isinstance(stream, DecompressorStream)` working
because the name is kept (Decision 1). No call-site logic changes.

### 7. Coordinate `seekable-gzip-and-block-writing` (no parallel subclass leaf)

BGZF plugs in as a `Decoder` + a forward-member-walk `build_index`, not a new
`DecompressorStream` subclass. The two changes coordinate: whichever lands second
targets the `Decoder` shape. This design leaves that plug explicitly open (the
forward-walk path in Decision 3).

## Risks / Trade-offs

| Risk | Mitigation |
| --- | --- |
| Subtle seek/SEEK_END/size/truncation regression | `test_seekable_streams.py`, `test_stream_inputs.py`, unix-compress `.Z` seek matrix are the gate; run all three dep configs before push |
| XZ progressive enrichment + `inner` save/restore inside `feed` | Decoder owns the save/restore (mirrors today's `_update_index`); base defensively restores in `_ensure_index_built` as now |
| unix-compress header-commit (origin `SeekPoint(0,3)` shift + gated cursor init) is the trickiest port | Land behind the `.Z` scenarios; the decoder emits the shifted origin point directly rather than the base mutating `_seek_points[0]` |
| Decoder-emits-absolute-points couples decoder to offsets | It is arithmetic over deltas the decoder already tracks, not new I/O; `recreate(point,…)` supplies the base offset |
| BGZF change forks seek semantics | Decision 7 — it re-targets onto `Decoder`; no new subclass leaf |
| XZ `stream_cell` late-bound closures reach into `stream._seek_points` / `_index_built` | Faithful port of the old subclass coupling; leave as-is for XZ. When BGZF needs the same, pass an explicit seek-table / index-state handle into `make_decoder` instead of another private-attr cell |

## Open Questions

- None blocking. The one micro-choice (unix-compress origin-point shift emitted by
  the decoder vs. a base helper) is settled during apply against the `.Z` scenarios.
