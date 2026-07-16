# Maintainer decisions

Four findings need a product/architecture call rather than a mechanical fix. (Both
parallel reviews raised the same set; consolidated here.)

## Q1 — seek-point collision (F1): fix the emitter, the merge, or both?

`add_seek_points`'s contract says "xz/lzip should filter [divergent-state collisions]
away," but the XZ decoder produces same-`decompressed_offset` points with different
`state`, tripping `assert False` (a raw `AssertionError`, outside the `ArchiveyError`
tree). Reachable two ways: a **valid** multi-stream `.xz` via seek-to-end-then-read (F1a),
and a **72-byte crafted** `.xz` with zero-`uncompressed_size` blocks that crashes
`build_index` directly (F1b).

- **(a)** Make the invariant true at the emitter — drop the redundant stream-start point
  when a block-bounds point covers the offset (F1a), and drop/coalesce zero-length blocks,
  or reject `uncompressed_size == 0` in `_parse_xz_index` (F1b; standard `xz` never emits
  empty blocks — confirm before rejecting).
- **(b)** Make `_resolve_same_offset_collision` total — resolve a divergent-state collision
  (prefer the block-bounds `state`) or translate it to `CorruptionError`, never
  `assert False`, so no codec and no hostile stream can turn a collision into a crash.

Recommendation: **both** (a to stop generating it, b to make hostile input safe). Should
the assert become a resolved-merge + diagnostic rather than a hard invariant?

## Q2 — accelerator damaged-input (F2): backstop deflate/zlib, or document the gap?

With `[seekable]` (rapidgzip) installed, a truncated standalone deflate/zlib/gzip stream is
silently returned as partial/zero data, where the stdlib backend raises `TruncatedError`
(VISION #3). In-archive ZIP members are still covered by downstream CRC/size verification
(but that turns truncation into an all-or-nothing error and loses the recoverable prefix
VISION #3 promises); the fully-exposed surface is standalone `RAW_STREAM` single-file
streams and any path with verification off.

- Accept and **document** the gap (lean on downstream CRC for the in-archive case), **or**
- Give the deflate/zlib accelerator path a completeness check (rapidgzip's own end-of-stream
  state, or a stdlib tail re-check: a stream that returns 0 bytes for non-empty input, or
  stops before EOS, should raise), and harden the gzip ISIZE backstop so a chance
  `1f 8b 08` in compressed data cannot mask a truncation and so a `BytesIO` source is also
  covered.

**Maintainer's proposal (2026-07-16), and it's the cleaner resolution:** when the
*decompressed* size is known (any central-directory format, plus xz/lzip index and gzip
ISIZE), a top-level length check catches the silent short read — this is the
`ArchiveStream`/`_wrap_member_stream` length-verification idea already registered in #113.
Then **gate AUTO on truncation-detectability**: select the accelerator when we can verify
completeness via a known decompressed size, else fall back to stdlib (which raises
natively). This turns F2 into a non-issue for the common case and confines the stdlib
fallback to exactly the exposed surface (standalone raw deflate/zlib with no declared size).

One subtlety worth pinning down in that design: it must be the **decompressed** size that
is verified, not the compressed one. rapidgzip reads the whole (truncated) compressed input
— "did we consume all compressed bytes" is trivially yes — so only "did we produce the
declared number of decompressed bytes" distinguishes a truncation. `compressed_input_size`
(the AUTO gate's ≥1 MiB input) does **not** by itself detect truncation; the length check
needs the expected *output* length. So the gate condition is "a decompressed size we can
verify against is available," which for raw deflate/zlib single-file streams it is not →
stdlib. Recommendation: adopt this.

Related: even the truncations rapidgzip *does* surface arrive as
`RuntimeError("std::exception")` and map to `CorruptionError`, not `TruncatedError`
(`codecs.py:292`) — should the accelerator translator distinguish these? (Minor if the
length check above lands, since it would raise `TruncatedError` itself.)

## Q3 — the per-read memory bomb (F3a): a base issue, not just LZW; + `maxbits` (F3b)

**The maintainer is right that this is not LZW-specific** — it is the base
`_read_decompressed_chunk` reading 64 KB of *compressed* input and buffering the **entire**
decoded result, so every `DecompressorStream`-based codec balloons on a `read(1)`. Measured
(all decode a 50 MB all-`'A'` payload, `read(1)` asks for one byte):

| codec (via `DecompressorStream`) | compressed input | buffer after `read(1)` | native output cap? |
| --- | --- | --- | --- |
| brotli   | **80 B** | 50 MB | none (`process()` decodes all) |
| xz / lzip / lzma-alone / raw-LZMA | 7.4 KB | 50 MB | `LZMADecompressor.decompress(data, max_length)` ✓ |
| deflate / zlib (stdlib path) | 48.6 KB | 50 MB | `decompressobj.decompress(data, max_length)` + `unconsumed_tail` ✓ |
| unix-compress (LZW) | 9.4 KB | 20 MB | our own decoder |
| ppmd | — | (same shape) | `Ppmd*Decoder.decode(data, length)` ✓ (currently called with `-1`) |
| deflate64 | — | (same shape) | `inflate64.Inflater.inflate(data)` — no obvious limit |

(The stdlib *file-object* codecs — gzip, bz2, lz4, zstd — are **not** affected: they read
incrementally, so `read(1)` peaks at ≤ 0.1 MB. lzma-**alone** via `LZMAFile` peaks ~8 MB,
already far below the `DecompressorStream` path.)

So the fix belongs in the **base**, answering the maintainer's "what about the others?":
give `Decoder.feed` an output budget and have it retain un-consumed input, decoding at most
~N bytes per call. Feasibility per backend, from the table:

- **zlib, lzma (xz/lzip/alone/raw), bz2, pyppmd** all expose a `max_length`/`length`
  bounded-decode plus internal retention of un-consumed input — the base can budget them
  directly (pyppmd is already `decode(chunk, -1)`; just pass a positive cap).
- **LZW** (our own): exactly the maintainer's idea — `feed` returns a "was the input
  exhausted?" flag; when it isn't, the base feeds no new compressed bytes next call and the
  decoder drains its retained input. This bounds it without touching the kernel's math.
- **brotli, deflate64** have no native output cap. brotli's `process()` decodes a fed chunk
  wholesale, so the only lever is feeding smaller compressed increments — which, at brotli's
  unbounded ratio (80 B → 50 MB above), does not tightly bound peak memory. These two
  optional-dep codecs would remain the residual; document, or wrap with a coarser guard.

**F3b (`maxbits`)** is the LZW-specific sub-part: clamp to 16 (raise `CorruptionError`
above — matching `compress`/`ncompress`); a one-liner that also caps the dictionary at 2¹⁶.

Recommendation: the `maxbits` clamp now (cheap, LZW-local); the output-budget is the real
fix and is broadly applicable — worth its own change, scoped to the `max_length`-capable
backends first (which covers the whole zero-dep core: deflate/zlib/xz/lzip/lzma), with
brotli/deflate64 documented as the residual.

## Q4 — `.Z` single-shot truncation (F4): raise directly on `read(-1)`/`readall()`

A truncated `.Z` is deferred via `pending_error` and **not** raised on a single-shot
`read(-1)`/`readall()` (only on the next empty read), while xz/lzip raise on the first
`read(-1)`.

**Maintainer's decision (2026-07-16), which I agree with:** the deferred-error contract
("`read(x)` returns all available bytes and raises on the *next* call, so no data is lost")
exists for the **chunked** reader, who *will* call again. A `read(-1)`/`readall()` caller
expects the returned value to be the *complete* stream and will not call again, so a
truncation must be raised on that first call. This makes `.Z` consistent with xz/lzip
(which already raise on the first `read(-1)`) at the cost of the partial bytes on that
specific call — acceptable, because the caller asked for the whole stream and it is
incomplete; a caller who wants the recoverable prefix reads in a bounded loop, where the
deferred "return bytes now, raise next" behaviour is preserved.

Fix location: the **base** — after `readall()`'s loop (and the `read(-1)` branch), if the
decoder has a `pending_error`, raise it instead of returning. That keeps the contract in
one place and covers any future deferred-error decoder; unix-compress's `flush()` need not
change. (The chunked `read(n)` path already raises correctly on the subsequent empty read,
so it is untouched.)
