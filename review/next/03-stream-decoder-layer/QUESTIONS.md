# QUESTIONS.md — maintainer decisions raised by Brief 3

These are the calls the review can't make unilaterally (they touch policy / spec, or a
"reject vs tolerate" trade-off). Ordered by the finding that raises them.

## Q1 (F1) — divergent same-offset seek-point collision: crash, or `CorruptionError`?

`_resolve_same_offset_collision` (`decompressor_stream.py:251`) `assert False`s when two
points share a `decompressed_offset` with genuinely divergent resume `state`. The #96 PR
body already flagged this as a follow-up; F1 shows it's reachable from a hostile `.xz`
(zero-`uncompressed_size` blocks) and, under `-O`, degrades to a silently-wrong seek.

**Decision needed:** which fix shape?
- (a) Translate the divergent collision to `CorruptionError` (keeps the file rejected,
  removes the crash). Minimal, but treats a legal-but-degenerate index as corrupt.
- (b) Coalesce/drop zero-length-decompressed points before `add_seek_points` (a zero-length
  block is never a useful seek target). Keeps such files readable.
- (c) Reject `uncompressed_size == 0` in `_parse_xz_index` (`xz.py:158`). Strictest — confirm
  no real encoder emits empty blocks first (standard `xz` does not, but do any 7z/embedded
  producers?).

My lean: (b) or (a). Whichever, the assert must not be the hostile-input boundary.

## Q2 (F2) — accelerated deflate/zlib truncation/corruption: acceptable, or must raise?

deflate/zlib through rapidgzip return 0/partial bytes with no error on damaged input, vs
`TruncatedError` from stdlib. There is no length trailer to backstop against.

**Decision needed:**
- Is it acceptable to rely on container-level CRC (ZIP/7z) to catch this, accepting that
  (i) standalone `.zlib` and verification-off paths stay silent and (ii) the recoverable
  prefix is lost either way? Or
- Should the deflate/zlib accelerator raise when rapidgzip returns no bytes / stops before
  EOS on a non-empty input? Or
- Should deflate/zlib acceleration be gated more narrowly (mirroring the gzip backstop's
  "seekable path with a verifiable structure" condition) so the silent path is never the
  default for large members?

This is the one that most directly contradicts a load-bearing VISION claim (#3), so it
probably wants an explicit spec line in `compressed-streams` either way.

## Q3 (F3) — LZW `maxbits` cap and an output budget for the decoder

Two sub-decisions:
- **Cap `maxbits` at 16?** Real `compress`/`gzip` do; `unix_compress.py:51` (and upstream
  `uncompresspy`) allow up to 31. Capping at 16 restores the format's natural dictionary
  ceiling and is a one-line change with (as far as I can tell) zero real-file impact.
- **Give the decoder an output budget?** The base buffers a whole `feed()` (F3a), so LZW's
  unbounded amplification balloons memory inside a single `read()`. Options: decode at most
  ~N bytes per `feed` (retaining unconsumed compressed input) so the base's chunking bounds
  peak memory; and/or extend the `max_ratio` guard (or a hard decompressed-size cap) to the
  stream layer / `stream_members`, not just `extract`. Is a stream-layer bomb guard in scope
  for v1, or is this a documented "use extraction limits" caveat?

## Q4 (F4) — `.Z` truncation on single-shot `read()`

Should `.Z` raise `TruncatedError` on the first `read(-1)`/`readall()` like xz/lzip, rather
than deferring to the next empty read? The fix is small (check `pending_error` in
`readall`, or raise directly from unix-compress `flush`). The only reason to keep the
current behavior is if some path deliberately wants "deliver bytes now, error later" — but
that seems to buy nothing here, since the leftover-bits signal is already known at flush
time. Confirm the deferred-`pending_error` mechanism is still needed at all, or whether it
can be simplified away for unix-compress.

## Cross-cutting: is a property-based seek test in scope? (F5)

Old finding #6 (a `seek(k); read(n) == plain[k:k+n]` property test across single/multi-
stream, multi-block, and empty-member fixtures) is still open. Adding it is low-risk and
would have caught F1. In scope for this cycle, or tracked as backlog?
