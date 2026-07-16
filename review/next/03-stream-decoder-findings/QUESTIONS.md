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

With `[seekable]` (rapidgzip) installed, a truncated **hash-less** accelerated
deflate/zlib/gzip stream is silently returned as partial/zero data, where the stdlib
backend raises `TruncatedError` (VISION #3).

**First, the exposure is narrower than a first read of F2 suggests — CRC-bearing members
are already covered.** `VerifyingStream` computes the container digest over the bytes read
and raises `CorruptionError` at clean EOF on a mismatch; a truncated accelerator read
delivers short bytes → wrong CRC → raise (verified: a `VerifyingStream` over a 6000-of-10000
byte inner raises `CorruptionError`). So **ZIP deflate/zlib members** (central-directory
CRC32, always present → `VerifyingStream` applied at `zip_reader.py:804`) and **single-member
gzip on a path** (trailer CRC surfaced by `GzipCodec.extract_metadata`) already turn the
accelerator's silent short read into an honest error — *and* keep the recoverable prefix,
since `VerifyingStream` delivers every chunk and raises only on the terminal empty read. The
genuinely silent surface is **hash-less** accelerated streams: standalone `RAW_STREAM` raw
deflate/zlib, and gzip where no trailer CRC is surfaced. That is exactly the niche the
`LengthVerifyingStream` below targets.

**Correction to my previous note in this file: #113 did *not* fold the check into
`ArchiveStream`.** During implementation (`1ee721a`) they reversed that plan and shipped a
contained **`LengthVerifyingStream`** (`internal/streams/verify.py`) on the RAR
forward-only path, wrapping only hash-less members. The implementer's stated reasons:

> the shared-SlicingStream/seek/partial-read interactions plus the ordering-vs-`VerifyingStream`
> masking made a contained `LengthVerifyingStream` on the RAR forward-only path the
> right-sized, low-blast-radius choice. It only wraps hash-less members (where
> `VerifyingStream` isn't the authority), which also fixed a real ordering bug where
> truncation was masking the correct `CorruptionError`/`EncryptionError`.

Concretely (`rar_reader.py:_wrap_payload_stream`): `LengthVerifyingStream(inner, size)` is
applied when `member.size is not None and not member.hashes and not is_seekable(inner)`,
composed as a **peer of** `VerifyingStream` (hashed → `VerifyingStream` is authoritative;
hash-less → `LengthVerifyingStream`), and its short-length verdict is deferred to `close()`
**after** the inner closes, so a more specific inner error (wrong password / `unrar` exit
code) wins — that was the masking bug. A global `ArchiveStream` check would have had to get
that ordering right for every backend and would have interacted with the shared-source
`SlicingStream`/seek paths — hence the smaller-blast-radius contained wrapper.

**So the resolution for F2 is: reuse `LengthVerifyingStream`, don't reopen the
`ArchiveStream` idea** (#113 tried it and backed off for concrete reasons). The reusable
primitive now exists and is format-agnostic (`BinaryIO` + `expected_size`). Apply it to the
hash-less accelerated codec streams, matching #113's compose-as-a-peer-of-`VerifyingStream`
pattern and its defer-verdict-to-`close()` ordering. Open design points, specific to us:

1. **The accelerator path is *seekable*** (rapidgzip is chosen precisely because AUTO
   requires declared seek demand), but `LengthVerifyingStream` is gated to
   `not is_seekable(inner)` on the RAR path. To reuse it here we'd wrap a seekable stream
   and lean on its self-disable-on-seek (it clears `_enabled` on the first `seek`), a mode
   #113 didn't exercise — decide whether to verify forward reads of seekable accelerated
   members (wrap + self-disable, same semantics as `VerifyingStream`) or keep the
   `not is_seekable` gate (which would exclude the accelerator entirely).
2. **Hash-less *and* size-known is a small set.** The remaining silent case is standalone
   raw deflate/zlib, which usually has **no** declared decompressed size either — so
   `LengthVerifyingStream` can't fire and the real backstop there is still **AUTO-gating to
   stdlib** when no verifiable size exists. gzip single-file without a surfaced trailer CRC
   is the other slice. So: `LengthVerifyingStream` where size is known; AUTO→stdlib
   otherwise.
3. **Keep the compressed-input clamp.** rapidgzip over-reads past EOS hunting a concatenated
   member (`DeflateCodec.open` comment, `codecs.py:950-952`), so the compressed
   `SlicingStream` bound must stay — unlike #113's RAR note that drops the pre-clamp to
   detect "too long." Our exposure is "too short," which `LengthVerifyingStream` catches
   without touching the input clamp.

The defeatable `_GzipTruncationCheckStream` (gzip-only, path-only, beaten by a chance
`1f 8b 08`) is then largely redundant: CRC-bearing gzip is covered by `VerifyingStream`,
hash-less size-known by `LengthVerifyingStream`, and the rest by AUTO→stdlib — so it can be
retired or kept only as a last-ditch gzip-specific guard.

Related: even the truncations rapidgzip *does* surface arrive as
`RuntimeError("std::exception")` and map to `CorruptionError`, not `TruncatedError`
(`codecs.py:292`) — should the accelerator translator distinguish these? (Minor once the
`ArchiveStream` length check lands, since it raises `TruncatedError` itself.)

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
~N bytes per call.

**The stdlib has a working reference for the zlib case: `gzip.GzipFile`.** It wraps the
*same* `zlib.decompressobj` archivey uses, yet `read(1)` peaks at ~0.1 MB, because
`_GzipReader.read(size)` (CPython `gzip.py`) threads the caller's `size` down as
`max_length` and pushes the un-consumed input back:

```python
buf = self._fp.read(io.DEFAULT_BUFFER_SIZE)              # (1) read ~8 KB compressed, not 64 KB
uncompress = self._decompressor.decompress(buf, size)   # (2) cap OUTPUT to `size` bytes
if self._decompressor.unconsumed_tail != b"":
    self._fp.prepend(self._decompressor.unconsumed_tail)  # (3) hold back what wasn't consumed
```

Direct demo on the 50 MB payload above: `decompress(comp[:65536])` (what archivey's
`ZlibDecoder.feed` does — no cap) returns **50,000,000 bytes**; `decompress(comp[:8192], 1)`
(what `_GzipReader` does on `read(1)`) returns **1 byte** and leaves 8,177 bytes in
`unconsumed_tail`. Same zlib object, opposite memory profile — the only differences are the
`max_length` argument and the smaller feed. archivey balloons for exactly those two reasons:
`ZlibDecoder.feed` calls `self._decomp.decompress(chunk)` with no `max_length`
(`decompress.py:35`), and the base feeds a 65536-byte compressed chunk
(`decompressor_stream.py:275`) and buffers all of it. `_compression.DecompressReader` (the
shared base for `lzma`/`bz2` file objects) does the same thing generically with
`self._decompressor.decompress(rawblock, size)` + `needs_input`, which is why `LZMAFile`/
`BZ2File` don't balloon either. Mirroring this in the `Decoder`/base is the fix.

One caveat: `GzipFile.read(-1)`/`readall()` calls `read(sys.maxsize)` — effectively an
unlimited `max_length` — so a *full* read-all still materializes everything (unavoidable).
gzip only bounds *bounded* reads; archivey's bug is that even a bounded `read(1)` balloons,
which the above shows is avoidable. Feasibility per backend, from the table:

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
