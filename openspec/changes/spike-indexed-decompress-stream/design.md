## Context

Today’s native indexed decompressors live under `internal/streams/`:

```
ReadOnlyIOStream
      │
DecompressorStream          ← buffer, pos, seek, SeekPoint table, EOF/size
      │            │
thin adapters      SegmentedDecompressorStream
(zlib/brotli/…)         │         │         │
                     lzip       xz        .Z
                                   │
                             SeekPoint.state → _XzBlockChain
```

Parallel world in `codecs.py`: `_AcceleratorStream` / stdlib gzip·bz2 / truncation
backstop — same *job* (bytes out of a compressed source), different hierarchy.

Caller contracts live in `seekable-decompressor-streams` (demand-driven indexes,
native xz/lzip indexes, CLEAR seek points, rewind diagnostics, seek-index
degradation). This change must preserve those; the problem is accidental hierarchy
cost, not missing behavior.

Provenance: explore session that mapped the hierarchy and proposed composition
(one stream + Decoder + SeekTable). Related in-flight: `seekable-gzip-and-block-writing`
(plans another `DecompressorStream` subclass for BGZF); `vendor-unix-compress-lzw`
(already split `LzwState` + stream — the pattern to generalize).

## Goals / Non-Goals

**Goals:**

- Lock the target architecture (composition over inheritance) and every open thread
  that would block a safe refactor.
- Run small spikes where a thread is empirically uncertain (esp. XZ progressive
  enrichment) and record the decision in this file before mechanical migration.
- After decisions: behavior-preserving refactor; delete `SegmentedDecompressorStream`
  and per-format stream subclasses; keep format parsers/state machines.
- Leave a clear plug shape for BGZF / future native zstd frame index.

**Non-Goals:**

- Changing public seek/truncation/diagnostic contracts (unless a spike *forces* a
  tiny clarification — default is zero caller-visible change).
- Folding rapidgzip / `_AcceleratorStream` into Decoder.
- Rewriting XZ/lzip/LZW parsers.
- Implementing BGZF or seekable-gzip writing in this change (coordinate only).
- “Half the size of `xz.py`” — most of that file is essential format complexity.

## Investigations

### Hierarchy tax vs essential complexity

| Piece | ~LOC | Nature |
| --- | --- | --- |
| `DecompressorStream` seek/buffer/index | ~250 | Essential — keep **once** |
| `SegmentedDecompressorStream` | ~90 | Thin adapter; disappears if every Decoder returns hints |
| 5× classes in `decompress.py` | ~187 | Boilerplate for 4 abstract methods |
| lzip/xz/.Z *stream subclass tails* | ~40–100 each | Glue that should not need inheritance |
| Format parsers + state machines | bulk of xz/lzip/unix_compress | Essential — leave |

### Three index-building paths (today)

1. **Progressive** — `add_seek_points` during `feed` / completed segments.
2. **One-shot** — `_build_index` on `SEEK_END` / seek past known frontier.
3. **XZ-only mid-read** — `_update_index` seeks `_inner`, scans that stream’s footer,
   restores position.

### Seek-point semantics disagree

| Format | On completed unit |
| --- | --- |
| lzip / xz member-start | **point first**, then advance cursors |
| unix-compress CLEAR | **advance first**, then point (resume *after* CLEAR) |

Both correct for their formats; the segmented base does not encode which. Folklore.

### XZ dual decoder

`SeekPoint.state: Any` is either `None` → `_XzState` or `_XzBlockBounds` →
`_XzBlockChain`. The stream type param is a union because resume strategy was
stuffed into the decoder type instead of `recreate(point)`.

### `.Z` leaks through the base

`UnixCompressDecompressorStream` overrides `read`, `_decompress_chunk`,
`_flush_decompressor`, `_reset_to_seek_point` for deferred `TruncatedError` and
header commit — the “sealed” segmented API is not sealed.

### Target shape (agreed direction)

```
┌─────────────────────────────────────────┐
│       IndexedDecompressStream           │  buffer, pos, eof, size, seek/read
│         HAS Decoder + SeekTable         │
└───────────────┬─────────────┬───────────┘
                ▼             ▼
         Decoder protocol   SeekTable
         feed/flush/        points / record(hint)
         finished/recreate  build_full / best_at
```

Every Decoder returns the same `DecodeOut(data, hints=…)`. Simple codecs always
emit empty hints → `SegmentedDecompressorStream` deletes itself.

## Decisions

### 1. Composition, not a smarter subclass tree

Replace `DecompressorStream` / `SegmentedDecompressorStream` / per-format stream
subclasses with **one** `IndexedDecompressStream` that owns I/O + seek/buffer/EOF
once, plus injected `Decoder` + `SeekTable`.

**Rejected:** keep inheritance and only tidy naming. **Rejected:** merge
accelerators into Decoder (foreign `BinaryIO`; lifecycle/`weakref.finalize` stays
at birth site).

### 2. Unify decoder shapes via ResumeHint

```python
@dataclass
class DecodeOut:
    data: bytes
    hints: list[ResumeHint] = field(default_factory=list)

@dataclass
class ResumeHint:
    # See Open Question A — absolute vs relative; default lean below.
    decompressed: int
    compressed: int
    state: Any = None  # XZ block bounds today; rare

class Decoder(Protocol):
    def recreate(self, point: SeekPoint) -> Decoder: ...
    def feed(self, chunk: bytes) -> DecodeOut: ...
    def flush(self) -> DecodeOut: ...
    @property
    def finished(self) -> bool: ...
```

Zlib/brotli/ppmd/deflate64/bcj → adapters, `hints=[]`.  
Lzip/xz/.Z → state machines already mostly exist; emit hints with **format meaning
baked in** (no more point-then-advance vs advance-then-point in stream subclasses).

**Rejected:** keep separate `_decompress_chunk → bytes` vs `feed → (bytes, units)`
forever. **Rejected:** zlib-style plus ad-hoc `take_clears()` (reinvents segmented).

### 3. SeekTable owns all three index paths

| Format | `record(hint)` | `build_full(inner, last)` |
| --- | --- | --- |
| zlib family | no-op | no-op (rewind from 0) |
| lzip | member starts | backwards trailer scan |
| xz | stream starts **+ progressive block enrichment** | backwards footer/index scan |
| unix-compress | CLEAR resumes | no-op (`SEEK_END` → base scan-to-EOF) |
| BGZF (future) | — | walk BC/MZ members |

XZ progressive enrichment moves from `_on_completed_segments` into
**XZ’s `SeekTable.record`** (may seek/restore `_inner`). Same behavior, one home.

Demand-driven: undeclared seekability → `NullSeekTable` (no points, no scans).

### 4. `recreate(point)` is the resume strategy

XZ’s `_XzState` vs `_XzBlockChain` becomes a choice inside `Decoder.recreate`, not
a union on the stream. No `_create_decompressor` / `_make_decompressor` dual API.

### 5. Deferred `.Z` TruncatedError is one stream hook, not four overrides

Decoder `flush` may set `pending_error`; `IndexedDecompressStream.read` raises it
on the next empty read after delivering bytes. Matches today’s contract; removes
the need to override chunk/flush/reset/read in the format stream class.

### 6. Spike-gated implementation

Do **not** start the mechanical migration until Open Questions A–D below are
closed (or explicitly deferred with a written fallback). Tasks §1 are the spike;
§2+ are the refactor.

### 7. Coordinate BGZF / seekable-gzip

`seekable-gzip-and-block-writing` MUST NOT land another `DecompressorStream`
subclass leaf in parallel. Either this change lands first and BGZF is a
Decoder+SeekTable policy, or BGZF waits / re-targets.

## Risks / Trade-offs

| Risk | Mitigation |
| --- | --- |
| Over-abstract SeekTable into a plugin framework | Cap at ~4–5 concrete policies; no registry of registries |
| XZ progressive enrichment fights clean `record` | Spike task: port `_update_index` behind SeekTable; keep existing seek tests green |
| Subtle SEEK_END / size / buffer regressions | Existing `test_seekable_streams` + unix-compress seek matrix are the gate; no “improve” seeks in the same PR |
| Naming churn (`IndexedDecompressStream` vs keep `DecompressorStream`) | Prefer rename that matches composition; keep a thin alias only if imports elsewhere hurt |
| Specs stay empty while validate wants a delta | Add a **shared seek-surface** clarifying requirement (parity across native indexed codecs) — no caller behavior change |

## Open Questions

These are the **loose threads that must be finalized before implementation**.
Each should leave this section when closed (move the answer into Decisions).

### A. Absolute vs relative ResumeHints

- **Options:** (1) Decoders emit **relative** `(decomp_delta, comp_delta)` and the
  stream’s single cursor helper converts to absolute SeekPoints; (2) Decoders emit
  **absolute** offsets (know cursors or receive them).
- **Lean:** (1) — keeps state machines I/O-agnostic (current xz/lzip/.Z already
  return relative units). Format-specific “before vs after” becomes which absolute
  the SeekTable records, not which way the Decoder orders cursor math.
- **Finalize when:** write the cursor helper + one lzip and one `.Z` SeekTable in a
  spike branch; confirm seek tests agree.

### B. XZ progressive enrichment under SeekTable.record

- **Question:** Can `_update_index` (seek inner → `_read_xz_index_backwards` for the
  just-finished stream → restore → add block SeekPoints with `state=bounds`) live
  entirely in `XzSeekTable.record` without the stream knowing about footers?
- **Lean:** Yes — `record` receives the completed-stream hint + access to `inner`
  and the live SeekPoint list; stream only calls `table.record` / `decoder.feed`.
- **Finalize when:** spike that deletes `_on_completed_segments` branching on
  `isinstance(_XzState)` and keeps `test_seekable_streams` / size-via-index tests green.

### C. Pending-error / header-commit hooks on Decoder

- **Question:** Is `pending_error` on Decoder enough for `.Z`, or does the stream
  need a tiny `after_flush` / `on_empty_read` callback protocol?
- **Lean:** `pending_error` property set by `flush`, cleared by stream after raise;
  header params for recreate live on the Decoder or a small side object owned by
  the unix-compress factory — not on the stream subclass.
- **Finalize when:** spike recreating deferred TruncatedError without overriding
  `read` on a format-specific stream class.

### D. Naming and module layout

- **Question:** Keep filename `decompressor_stream.py` and rename the class to
  `IndexedDecompressStream`, or rename module too? Where do `Decoder` adapters live
  (`decompress.py` vs per-codec modules)?
- **Lean:** Class rename to `IndexedDecompressStream` (or keep `DecompressorStream`
  as the one stream class name to limit churn — pick one in spike); module can stay
  until a follow-up. Adapters stay beside codecs that need them; zlib-family thin
  adapters can remain in one `decompress.py` as functions/classes implementing
  Decoder only.
- **Finalize when:** grepping import sites (`codecs.py`, tests, single_file_reader)
  and choosing the lower-churn name in Decisions.

### E. (Optional defer) Native zstd frame index

Out of scope for this change’s implementation, but the SeekTable plug shape should
not paint us into a corner. Confirm the design doesn’t assume “backwards trailer
only” — progressive CLEAR-like points and one-shot member walks (BGZF) must both fit.
No spike required beyond a written note in Decisions once A–D close.
