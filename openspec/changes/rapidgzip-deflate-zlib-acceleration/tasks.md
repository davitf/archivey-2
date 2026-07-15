## 1. Benchmark the AUTO size threshold

- [ ] 1.1 Add a benchmark (scripts/ or a bench test) decoding+seeking raw deflate, zlib, and gzip
      via rapidgzip vs stdlib across compressed sizes (~1 KiB → ~10 MiB) on Linux and macOS
- [ ] 1.2 Identify the crossover size where rapidgzip's per-stream setup is repaid; account for the
      many-small-members aggregate case
- [ ] 1.3 Choose a single conservative `AUTO` threshold constant and record its value + rationale
      in design.md and the `use_rapidgzip` user docs

## 2. AUTO minimum-size gate

- [ ] 2.1 Thread the known compressed input size into accelerator selection (widen
      `AcceleratorMode.enabled_for` with an optional size, or gate at the codec call sites — per
      design Decision 3), keeping gzip/bzip2/deflate/zlib on one gate
- [ ] 2.2 Apply the threshold only under `AUTO`; `ON` ignores it, `OFF` disables; unknown size keeps
      pre-threshold behaviour
- [ ] 2.3 Wire the gzip call site (`GzipCodec.open`) to the size-aware gate as well

## 3. rapidgzip acceleration for deflate/zlib

- [ ] 3.1 Add the rapidgzip branch to `DeflateCodec.open` and `ZlibCodec.open`, mirroring
      `GzipCodec.open`: `enabled_for(...)` check, unwrapped `rapidgzip.open(source)`,
      `_AcceleratorStream` close-guard; retain `ZlibDecompressorStream` fallback
- [ ] 3.2 Route both codecs through the accelerator exception translator so corrupt input →
      `CorruptionError` (never a raw rapidgzip exception); no ISIZE-style backstop added
- [ ] 3.3 Update the slow-rewind diagnostic so stdlib-fallback zlib/deflate names the `[seekable]`
      accelerator, consistent with gzip

## 4. Tests

- [ ] 4.1 Parity: accelerated deflate/zlib decode + mid-stream seek match stdlib output (declared
      seekable, accelerator on)
- [ ] 4.2 Gating: `OFF`, accelerator-absent, and below-`AUTO`-threshold all use stdlib; `ON` forces
      rapidgzip below the threshold
- [ ] 4.3 Error translation: corrupt deflate/zlib body → `CorruptionError`; assert the standalone
      mid-cut truncation limitation is the documented behaviour
- [ ] 4.4 Bounded-input: a deflate stream with trailing bytes fed through the codec's bounded slice
      decodes correctly (no over-read error)
- [ ] 4.5 Run the suite in `[all]`, `[all-lowest]`, and `[core-only]` (core-only exercises the
      stdlib fallback path)

## 5. Verify

- [ ] 5.1 `uv run pyrefly check` and `uv run ty check` stay clean
- [ ] 5.2 `openspec validate --strict rapidgzip-deflate-zlib-acceleration`
