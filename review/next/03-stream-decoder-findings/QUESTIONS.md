# Maintainer decisions

Three findings need a product/architecture call rather than a mechanical fix.

## Q1 — XZ seek-point collision (F1): fix the emitter, the merge, or both?

`add_seek_points`'s contract says "xz/lzip should filter [divergent-state collisions]
away," but the XZ decoder emits a stream-start point (`state=None`) and a first-block
point (`state=<block bounds>`) at the same `decompressed_offset`, tripping `assert False`
(a raw `AssertionError`, outside the `ArchiveyError` tree) on a valid multi-stream `.xz`.

- **(a)** Make the invariant true — stop the XZ decoder emitting the redundant
  stream-start point when a block-bounds point covers that offset. Keeps the assert as a
  genuine tripwire.
- **(b)** Make `_resolve_same_offset_collision` resolve divergent-state collisions
  (prefer the block-bounds `state`) instead of asserting — so no codec, and no hostile
  stream, can turn a seek-point collision into a process crash.

Recommendation: **both** (a to stop generating it, b to make hostile input safe). Which
do you want, and should the assert become a resolved-merge + a diagnostic rather than a
hard invariant?

## Q2 — Accelerator truncation (F2): backstop deflate/zlib, or document the gap?

With `[seekable]` (rapidgzip) installed, a truncated standalone deflate/zlib/gzip stream
is silently returned as partial data, where the stdlib backend raises `TruncatedError`
(VISION #3). In-archive ZIP members are still covered by downstream CRC/size
verification; the exposed surface is standalone `RAW_STREAM` single-file streams.

- Accept and **document** that accelerated standalone deflate/zlib/gzip does not surface
  truncation (lean on downstream CRC for the in-archive case), **or**
- Add a truncation backstop to the deflate/zlib accelerator path and harden the gzip
  ISIZE backstop so a chance `1f 8b 08` in compressed data cannot mask a truncation.

Related: even the truncations rapidgzip *does* raise arrive as `RuntimeError("std::exception")`
and are mapped to `CorruptionError`, not `TruncatedError` (`codecs.py:292`). Should the
accelerator translator distinguish these, or is corruption-vs-truncation not worth
separating for the accelerated path?

## Q3 — `.Z` `maxbits` upper bound (F4): clamp to 16, or accept up to 31?

archivey accepts `maxbits` 17–31, which real `compress`/`ncompress` reject (the format
ceiling is 16). Growth stays input-proportional, so it is not an OOM bomb — the question
is whether archivey should reject out-of-spec `.Z` (raise `CorruptionError` for
`maxbits > 16`, matching the ecosystem and bounding the code table to the standard 2¹⁶),
or intentionally be lenient. Recommendation: clamp to 16.
