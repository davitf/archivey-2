## Why

`rapidgzip` (already the `[seekable]` gzip/bzip2 accelerator) natively decodes raw
DEFLATE and zlib-wrapped DEFLATE as of 0.16.0 — it auto-detects `GZIP`/`ZLIB`/`DEFLATE`
and gives the same parallel decode + random access it gives gzip, with **no gzip
wrapper needed** (empirically verified against the pinned 0.16.0). Today the `deflate`
and `zlib` codecs always use stdlib `zlib`, so declared-seekable deflate/zlib streams —
including the ZIP DEFLATE members that `zip-native-codec-streams` will route through the
`deflate` codec — get no accelerator. Wiring rapidgzip into these two codecs extends the
existing gzip acceleration to the whole DEFLATE family for one small, mechanical change.

## What Changes

- Add a rapidgzip acceleration path to the `deflate` and `zlib` codecs, gated exactly like
  gzip (`use_rapidgzip` × declared seekability × package availability), wrapped in the same
  `_AcceleratorStream` close-guard. The default **sequential** backend stays stdlib `zlib`;
  this only adds the seekable/parallel path. Not ZIP-specific — any deflate/zlib stream benefits.
- Extend the accelerator error-translation and rewind-warning contracts to cover
  rapidgzip-backed deflate/zlib (today "zlib records no accelerator").
- **Add an `AUTO` minimum-input-size gate** for the rapidgzip accelerator across all three
  members of the family (deflate + zlib + gzip): under `AUTO`, only select rapidgzip once the
  known compressed input size exceeds a benchmarked threshold, so archives of many tiny
  members don't pay rapidgzip's per-stream index/thread setup for no gain. `ON` still forces
  it; `OFF` still disables it. The threshold value is determined by a benchmark in this change.
- Record the truncation/checksum gap: rapidgzip does **not** validate zlib's Adler-32 and
  returns a silent short read on mid-stream truncation, so a standalone zlib/deflate stream
  loses stdlib `zlib`'s truncation detection unless backstopped (see design).

## Capabilities

### New Capabilities

<!-- none -->

### Modified Capabilities

- `seekable-decompressor-streams`: the rapidgzip accelerator extends to raw DEFLATE and
  zlib-wrapped DEFLATE (not gzip/bzip2 only); accelerator error translation and slow-rewind
  diagnostics account for deflate/zlib; `AUTO` gains a minimum-input-size threshold before it
  selects rapidgzip.

## Impact

- `internal/streams/codecs.py`: `DeflateCodec.open` / `ZlibCodec.open` grow a rapidgzip branch
  mirroring `GzipCodec.open`; accelerator error translator and rewind-warning reused for both.
- `config.py`: `AcceleratorMode.enabled_for` (or the codec call sites) gains a size input so
  `AUTO` can apply the minimum-size threshold; new threshold constant.
- No new dependency or extra — reuses `[seekable]` `rapidgzip>=0.16.0`.
- Tests: deflate/zlib acceleration parity + error-translation cases; benchmark to pick and
  document the `AUTO` size threshold; corruption/truncation behaviour for accelerated deflate/zlib.
- Interacts with `zip-native-codec-streams` (its ZIP DEFLATE members become accelerable) and
  `rapidgzip-truncation-investigation` (the truncation-backstop discussion extends to zlib/deflate).
