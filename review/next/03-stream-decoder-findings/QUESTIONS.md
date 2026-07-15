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

Related: even the truncations rapidgzip *does* surface arrive as
`RuntimeError("std::exception")` and map to `CorruptionError`, not `TruncatedError`
(`codecs.py:292`) — should the accelerator translator distinguish these?

## Q3 — LZW bomb + `maxbits` (F3): cap and budget?

A ~9 KB `.Z` buffers ~20 MB on a single `read(1)` (unbounded, position-dependent LZW
amplification, no per-`feed` output budget), worsened by `maxbits` accepted up to 31 (format
ceiling is 16, dictionary ceiling 2³¹). `stream_members`/forward iteration apply no bomb
guard.

- Clamp `maxbits` to 16 (raise `CorruptionError` above — matching `compress`/`ncompress`),
  **and/or**
- Give the LZW decoder an output budget (decode at most ~N bytes per `feed`, retaining
  un-consumed compressed input) so the base's existing 64 KB chunking actually bounds peak
  memory per read.

Recommendation: both — the `maxbits` clamp is a one-liner; the output budget is the real
fix for peak memory and would also make `read(n)` cost roughly proportional to `n`.

## Q4 — `.Z` single-shot truncation (F4): fix the base or the decoder?

A truncated `.Z` is deferred via `pending_error` and **not** raised on a single-shot
`read(-1)`/`readall()` (only on the next empty read), while xz/lzip raise on the first
`read(-1)`. Fix in the base (`readall`/`read(-1)` check `pending_error` after the final
chunk) so the contract holds for any future deferred-error decoder, or in unix-compress
(raise from `flush()` directly like the other seekable decoders, since the leftover-bits
signal is known at flush time)? Recommendation: fix the base — it is where the contract
lives.
