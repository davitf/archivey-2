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
  that would block a safe refactor. **A/C/D/E locked; B (XZ enrichment placement)
  still open.**
- Spike Open Question B and record the decision here before mechanical migration.
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

### 2. Unify decoder shapes via relative ResumeHints (locked)

Decoders emit **relative** `(decomp_delta, comp_delta)` units. A SeekTable policy
turns them into absolute `SeekPoint`s (`before` = lzip/xz stream boundary;
`after` = unix-compress CLEAR). Enrichment (xz blocks, BGZF member walks) may
also inject absolute points from index/trailer scans — those do not come from
relative units.

```python
@dataclass
class DecodeOut:
    data: bytes
    hints: list[ResumeHint] = field(default_factory=list)  # relative sizes

@dataclass
class ResumeHint:
    decompressed: int  # delta since previous hint / origin
    compressed: int
    state: Any = None  # rare; xz block bounds today

class Decoder(Protocol):
    def recreate(self, point: SeekPoint) -> Decoder: ...
    def feed(self, chunk: bytes) -> DecodeOut: ...
    def flush(self) -> DecodeOut: ...
    @property
    def finished(self) -> bool: ...
    @property
    def pending_error(self) -> BaseException | None: ...
```

Zlib/brotli/ppmd/deflate64/bcj → adapters, `hints=[]`, `pending_error` always
`None`. Lzip/xz/.Z → existing state machines; format meaning lives in the
SeekTable policy, not in stream-subclass cursor folklore.

**Rejected:** absolute offsets from Decoders (couples them to cursors/I/O).
**Rejected:** keep separate `_decompress_chunk → bytes` vs `feed → (bytes, units)`.
**Rejected:** duck-typing `truncated` / ad-hoc attributes — use the Protocol property.

### 3. SeekTable owns index paths (shape of xz enrichment still open — see B)

| Format | Progressive `record` | `build_full` |
| --- | --- | --- |
| zlib family | no-op | no-op (rewind from 0) |
| lzip | member starts (`before`) | backwards trailer scan |
| xz | stream starts + **block enrichment** (how: Open Question B) | backwards footer/index scan |
| unix-compress | CLEAR resumes (`after`) | no-op (`SEEK_END` → base scan-to-EOF) |
| BGZF (future) | — | forward member walk via BC/MZ |

Demand-driven: undeclared seekability → `NullSeekTable` (no points, no scans).

### 4. `recreate(point)` is the resume strategy

XZ’s `_XzState` vs `_XzBlockChain` becomes a choice inside `Decoder.recreate`, not
a union on the stream. No `_create_decompressor` / `_make_decompressor` dual API.
(XZ recreate may need the table’s subsequent block points — factory closure or
`recreate(point, table)`; settled with Open Question B.)

### 5. Deferred `.Z` TruncatedError via formal `pending_error` (locked)

`Decoder.pending_error` is a real Protocol property (not duck-typed). After
`flush`, unix-compress sets it to `TruncatedError` when leftover bits are
nonzero; other Decoders leave it `None`. `DecompressorStream.read` raises and
clears it on the next empty read after delivering bytes. Header params for
CLEAR recreate live on the Decoder / unix-compress factory; origin
`SeekPoint(0, 3)` adjustment is SeekTable’s job — no format stream subclass
overriding `read` / chunk / flush / reset.

### 6. Keep the name `DecompressorStream` (locked)

The one composed stream class stays **`DecompressorStream`** — it is already the
external vocabulary (specs, docs, `isinstance` / mental model) and should not
expose “Indexed…” implementation detail in the type name. Module
`decompressor_stream.py` stays. A later interface/implementation split is
allowed if it earns its keep; not required for this change.

Zlib-family Decoder adapters stay in `decompress.py`; xz/lzip/.Z keep parsers in
their modules and lose stream-subclass tails. Construction sites in `codecs.py`
become thin factories wiring `(Decoder, SeekTable)` into `DecompressorStream`.

**Rejected:** rename to `IndexedDecompressStream` (leaks structure; churn for
little clarity).

### 7. Spike-gated implementation

Do **not** start the mechanical migration until **Open Question B** is closed
(or explicitly deferred with a written fallback). Tasks §1.2 are the remaining
spike; §2+ are the refactor. Threads A/C/D/E are locked above.

### 8. BGZF / zstd seekable fit + coordinate seekable-gzip (locked)

SeekTable is **not** backwards-trailer-only: it must support progressive points
(CLEAR-like), one-shot backwards scans (lzip/xz), and one-shot forward member
walks (BGZF/mgzip via BC/MZ; zstd seekable footer table). That is enough for
`seekable-gzip-and-block-writing` to plug in as Decoder + SeekTable without a
new `DecompressorStream` subclass leaf.

`seekable-gzip-and-block-writing` MUST NOT land another subclass leaf in
parallel — wait on or re-target this composition model.

## Risks / Trade-offs

| Risk | Mitigation |
| --- | --- |
| Over-abstract SeekTable into a plugin framework | Cap at ~4–5 concrete policies; no registry of registries |
| XZ progressive enrichment fights clean `record` | Open Question B; spike before migration; keep seek tests green |
| Subtle SEEK_END / size / buffer regressions | Existing `test_seekable_streams` + unix-compress seek matrix are the gate |
| Accidental rename pressure | Decision 6: keep `DecompressorStream` |

## Open Questions

### B. Where does XZ block enrichment live?

Today, when a multi-stream XZ file finishes a stream during a forward read, the
stream subclass seeks the compressed file, reads that stream’s footer/index, and
registers block-level `SeekPoint`s (with `state=block bounds`) — then restores
the file position. That is “progressive enrichment.” On a later seek into a
block, `recreate` builds `_XzBlockChain` from those points.

**Still open:** who owns that enrichment after the refactor — a fat SeekTable, a
thin table plus a stream hook, or a separate Enricher? Options and trade-offs are
being explored in the change discussion; lock one here before §2.

**Also must handle:** while replaying via `_XzBlockChain`, completed units only
advance cursors (no new points). `recreate` needs subsequent block points from
the table.
