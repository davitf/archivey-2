# Brief 3 — Seekable decoder layer & accelerators: correctness after the refactor

Read `review/next/README.md` first — **especially** the "overturned conclusions"
note. This brief reviews the seekable-decompression layer **as it now exists** after
the composition refactor and the accelerator/codec additions. It is a correctness
and hostile-input review of the new shape. It is **not** a re-litigation of whether
to refactor — that decision is made, shipped (#96), and documented.

## Why this review, now

The old `deep-simplification.md` told the team the codec/stream layer was the
exemplar and "the first review was right to protect it." Three things then happened
that the old review never saw:

1. **#96 collapsed the hierarchy** (`DecompressorStream → SegmentedDecompressorStream
   → per-codec subclasses`) into **one concrete `DecompressorStream` + a `Decoder`
   strategy** (+1191/−518). New surface: the `Decoder` protocol, `DecodeOut`,
   `recreate`/`pending_error`/`build_index`, and decoder-emitted absolute
   `SeekPoint`s. Design: `openspec/changes/archive/2026-07-14-decompressor-stream-composition/design.md`.
2. **#105 put rapidgzip on the hot path** — the C++ accelerator (own worker threads,
   `weakref.finalize` lifecycle) now backs **deflate/zlib**, the commonest codecs,
   behind an **AUTO size gate** (#102/#105). It was previously gzip/bzip2 only.
3. **#89 vendored a hand-written LZW decompressor into the zero-dep core**
   (`internal/streams/unix_compress.py`, adapted from `uncompresspy`) — untrusted
   `.Z` parsing in the *trusted* core with no optional-dep shield. **#88** added the
   LZMA-Alone standalone stream.

The layer the review protected is now both simpler in structure and carrying more
attacker-facing, concurrency-sensitive, and perf-critical code. That combination is
exactly where a "we already reviewed this" assumption goes wrong.

## Files (traced)

- `internal/streams/decompressor_stream.py` — the collapsed stream: `Decoder`
  protocol, `DecodeOut`, `BaseDecoder`, `SeekPoint`, the seek engine, demand-driven
  index orchestration, `pending_error` handling.
- `internal/streams/decompress.py` — forward-only decoders (zlib/brotli/ppmd/bcj/
  deflate64).
- `internal/streams/xz.py`, `lzip.py` — the seekable XZ (dual `_XzState` /
  `_XzBlockChain`) and lzip decoders + their `build_index`/enrichment paths.
- `internal/streams/unix_compress.py` — vendored LZW (`UnixCompressDecoder`), CLEAR
  seek-points, deferred-truncation `pending_error`.
- `internal/streams/codecs.py` — the codec dispatch table **and** the accelerator
  wrappers (`_AcceleratorStream`, `_GzipTruncationCheckStream`, the rapidgzip/
  IndexedBzip2 path, the AUTO size gate, the per-platform rapidgzip error-message
  tables).
- Specs: `openspec/specs/compressed-streams/spec.md`,
  `seekable-decompressor-streams`. Design docs for #96 and (context) PR #92 spike.

## What to hunt (ranked)

### A. Seek-index correctness across the four discovery paths (top priority)
The refactor moved index discovery *onto the decoder*, which now emits absolute
`SeekPoint`s. The design enumerates four discovery shapes that "all must survive":
progressive boundary, progressive enrichment (XZ footer scan mid-`feed`), one-shot
backward scan (`SEEK_END`), and the forward member walk (BGZF-shaped, not yet
landed). Hunt:
- **Before/after placement asymmetry** (lzip/xz put the point *before* advancing
  cursors; unix-compress CLEAR puts it *after*). The base is supposed to be
  format-agnostic now — verify no format leaked back into the base, and that a
  decoder emitting a point at the wrong side produces a silently-wrong seek result
  (wrong bytes, not an error).
- **Origin refinement "last-wins"** and the **assert on non-origin collisions** the
  #96 PR body flagged as a follow-up: can a crafted multi-member stream produce two
  seek points at the same `decompressed_offset` with *different* resume state and
  trip the assert (→ crash on hostile input) or, worse, resolve last-wins to the
  wrong resume data?
- **XZ enrichment `inner` save/restore inside `feed`**: the decoder seeks the shared
  inner stream to scan a footer and must restore position. Under the streaming
  contract and under CONCURRENT, is that save/restore airtight, or can a concurrent
  reader observe the moved position?
- **XZ `stream_cell` late-bound closures** reaching into `stream._seek_points` /
  `_index_built` — the design flags this as a faithful-but-ugly port. Is it correct,
  and does it break if `build_index` runs concurrently with `feed`?
- A **seek-math property test** (old finding #6, still open): does one exist now? If
  not, that's a test-coverage finding on the highest-risk arithmetic in the layer.

### B. Vendored LZW hostile-input (`unix_compress.py`) — core, no shield
This is hand-written decompression of untrusted bytes in the zero-dep core, so
VISION claim #2 applies with full force and no `[extra]` gate to hide behind.
- Code-width growth (9→16 bits), the code table, and CLEAR handling: can a crafted
  `.Z` cause unbounded table growth, an index past the table (KwKwK edge), or an
  OOM? Is the dictionary size bounded by the `maxbits` header field, and is that
  field itself bounded?
- The deferred-`TruncatedError` via `pending_error`: is it always eventually raised
  (not swallowed) and never raised *before* the valid decoded bytes are delivered
  (VISION #3)? Does a seek reset clear it correctly (the design says the base clears
  on reset — verify)?
- Provenance/licensing: the BSD-3 `uncompresspy` notice is in-file — confirm it's
  intact and the vendoring is complete (no runtime import of the upstream package).

### C. Accelerators on the hot path (`codecs.py`)
- **Lifecycle:** `_AcceleratorStream` must `close()` rapidgzip's C++ threads before
  interpreter exit (`weakref.finalize`). Now that it's on deflate/zlib (far more
  members), is the finalizer still the birth-site one, and can a million-member
  dedupe sweep leak threads/FDs if streams aren't explicitly closed? Old finding D2
  (lazy `capture_open_site`) territory.
- **AUTO size gate** (#102/#105): the policy that decides accelerate-vs-stdlib by
  size. Is the threshold honest against the ≤1.3× VISION perf budget (does the
  benchmark gate #100 actually exercise both sides of the gate?), and is there a
  correctness cliff at the boundary (a member right at the threshold decoded two
  different ways must produce identical bytes)?
- **Truncation backstop:** `_GzipTruncationCheckStream` — does the deflate/zlib
  application preserve the truncated-input → typed-error behavior, or can rapidgzip
  swallow a truncation the stdlib path would have caught?
- **Free-threading:** the old review's stated position was "accelerators are
  GIL-only, don't promise otherwise." Now that they're on the commonest codec, is
  that boundary still honestly documented and enforced (serialized via
  single-live-stream / handle lock), or has the hot-path change quietly widened what
  runs under `3.13t`?
- The per-platform rar/rapidgzip **error-message tables** (the design called these
  irreducible) — are they still matched correctly against the pinned floor
  (`rapidgzip>=0.16.0`) in the `[all-lowest]` leg?

### D. `Decoder` protocol contract & error translation
- `pending_error` is now a real protocol property raised/cleared by the base. Trace
  every `pending_error` producer/consumer: is any error dropped on a `flush` that
  returns no bytes, or on an early close? Does an unrecognized decoder exception
  propagate raw per the no-catch-all rule, or does the base over-catch?
- `recreate(point, inner)` on seek reset: does any decoder carry state across
  `recreate` that should have been reset (buffers, CTR/CRC accumulators, `inner`
  position)?
- LZMA-Alone (#88) and the LZMA1+BCJ staging (`pybcj`, BPO-21872 workaround): confirm
  the staged path still can't hit liblzma's silent BCJ truncation, and that the
  standalone LZMA-Alone size/EOS handling is correct on truncated input.

## Non-goals / already settled
- **Do not re-propose or re-evaluate the composition refactor**, the SeekTable-vs-
  decoder decision, or merging the five forward-only adapters — all decided in the
  #96 design (Decisions 1–5, with rejections recorded). Reviewing whether the
  *result* is correct is in scope; second-guessing the *choice* is not.
- BGZF is explicitly a future `Decoder` plug (#96 Decision 7) — its absence is not a
  finding; but the forward-walk `build_index` path it will use *is* in scope for
  "does the current shape actually admit it without base changes."
- The codec descriptor table structure and `streamtools` are intended as-is.

## Deliverable
Per README. Suggested theme files: `seek-index.md` (the headline arithmetic/hostile
analysis), `vendored-lzw.md`, `accelerators.md`. Trace every finding to `file:line`
with the concrete stream/seek sequence or crafted `.Z`/`.xz` that triggers it, and
the dependency config. For seek-math and LZW claims, a failing property test or
adversarial fixture beats prose. Include the "what's actually fine" section — the
refactor may well be clean, and saying so precisely is a useful result.
