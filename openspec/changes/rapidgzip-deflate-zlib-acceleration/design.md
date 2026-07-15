## Context

`GzipCodec.open` (codecs.py:516) branches on `use_rapidgzip.enabled_for(seekable, available)` and,
when enabled, wraps `rapidgzip.open(source)` in `_AcceleratorStream` (a `weakref.finalize`
close-guard) plus, for path sources, `_GzipTruncationCheckStream` (ISIZE backstop). `DeflateCodec`
(codecs.py:868) and `ZlibCodec` (:880) have no such branch — they unconditionally return
`ZlibDecompressorStream(source, wbits=-15 | MAX_WBITS)` over stdlib `zlib`. Accelerator selection
is `AcceleratorMode.enabled_for(*, seekable, available)` (config.py) — a pure tri-state with no
size input. The `[seekable]` extra pins `rapidgzip>=0.16.0`.

Provenance: this change spun out of the `zip-native-codec-streams` investigation — `rapidgzip`
0.16.0 turned out to decode raw DEFLATE and zlib natively, not just gzip, so the acceleration is a
whole-DEFLATE-family concern rather than a ZIP detail. Related in-flight work:
`rapidgzip-truncation-investigation` (the gzip ISIZE backstop) and `zip-native-codec-streams`
(routes ZIP method-8 members through `DeflateCodec`).

## Goals / Non-Goals

**Goals:**
- Add a rapidgzip acceleration path to the `deflate` and `zlib` codecs, gated like gzip.
- Extend accelerator error-translation and slow-rewind diagnostics to deflate/zlib.
- Introduce a benchmarked `AUTO` minimum-input-size threshold for the whole DEFLATE family
  (deflate + zlib + gzip) so tiny members don't pay accelerator setup.

**Non-Goals:**
- Changing the default sequential backend (stays stdlib `zlib`).
- A truncation backstop for standalone zlib/deflate (accepted gap; see Decision 4).
- Any ZIP-specific wiring — that lives in `zip-native-codec-streams`, which merely benefits.
- Revisiting the gzip ISIZE backstop (owned by `rapidgzip-truncation-investigation`).

## Investigations

All measured against the pinned `rapidgzip==0.16.0` in this repo's `[seekable]` extra.

**Format support** — `rapidgzip.determineFileType()` reports `GZIP`/`ZLIB`/`DEFLATE`; the
`RapidgzipFile` constructor takes **no** format/`wbits` argument (detection is automatic):

| Input | `determineFileType` | Parallel decode | Seek into middle |
| --- | --- | --- | --- |
| gzip (`gzip.compress`) | `GZIP` | ✅ correct | ✅ |
| zlib (`zlib.compress`, wbits 15) | `ZLIB` | ✅ correct | ✅ |
| raw deflate (wbits −15) | `DEFLATE` | ✅ correct | ✅ |

Confirmed end-to-end for the container case: a raw-deflate blob embedded in a larger buffer
(local header before, `PK\x03\x04` after) read through a bounded slicing stream decoded and
seeked correctly — the exact mechanism `zip-native-codec-streams` uses.

**Over-read** — rapidgzip continues past a DEFLATE end-of-stream looking for a concatenated
member. `BytesIO(raw)` (exact length) → clean decode + stop at EOF. `raw + trailing_bytes` →
`RuntimeError: Invalid deflate block found!`. So the input must be exactly bounded.

**Truncation / checksum** — no reliable truncation signal, and zlib's Adler-32 is not checked:

| Input | rapidgzip result |
| --- | --- |
| zlib, corrupt Adler-32 byte | decodes fully, **no error** (Adler not validated) |
| zlib, corrupt DEFLATE body | `RuntimeError` (isal error code −3) |
| zlib, tail/`-1B` cut | `RuntimeError` "Unexpected end of file" |
| zlib, mid-stream cut | **silent** `len=0` short read |
| raw deflate, corrupt body | `RuntimeError` (isal −3) |
| raw deflate, mid/`-1B` cut | **silent** short read |

Contrast: stdlib `zlib` raises `error: incomplete or truncated stream` on these truncations —
so moving standalone zlib/deflate onto rapidgzip *loses* that truncation detection.

## Decisions

### 1. Wire rapidgzip into `DeflateCodec`/`ZlibCodec`, mirroring `GzipCodec`
Add the same `use_rapidgzip.enabled_for(...)` branch, `_AcceleratorStream` wrap, and
accelerator exception translator to both codecs; pass the source through unwrapped (rapidgzip
auto-detects). Default sequential path (`ZlibDecompressorStream`) is retained for the disabled/
unavailable/below-threshold cases. **Rejected:** wrapping deflate/zlib in a synthetic gzip
header+footer — unnecessary given native detection, and it would force a fake CRC/ISIZE.

### 2. The input handed to rapidgzip must be exactly bounded
Rely on the caller's bounded stream (container `SlicingStream` sized to the compressed length;
a whole-file source for standalone zlib) so rapidgzip hits real EOF at the stream end rather than
over-reading into trailing bytes. This is already true for the ZIP path and for single-file
sources; the codec adds no unbounded wrapper.

### 3. `AUTO` gains a minimum-input-size threshold (the benchmark wrinkle)
Extend accelerator selection so `AUTO` only picks rapidgzip once the *known* compressed input
size reaches a threshold; below it, use stdlib `zlib`/`gzip`. `ON` ignores the threshold; `OFF`
disables. Rationale: rapidgzip spins up worker thread(s) and a chunk index per stream — pure loss
for the many-tiny-members case (small ZIP entries, small gzip files). The threshold keys off the
*compressed* size because that is what is knowable at open time (file size / slice length); when
size is unknown, keep pre-threshold behaviour (enable when otherwise eligible).

Mechanically, thread the size into selection — either widen `AcceleratorMode.enabled_for` with an
optional `input_size`/`min_size`, or apply the comparison at the codec call sites. Prefer the
former so gzip/bzip2 share one gate. The threshold is a named constant, its value fixed by the
benchmark in Open Questions and documented where `use_rapidgzip` is described.
**Rejected:** gating on *uncompressed* size (not known ahead of decode) or a fixed hardcoded
member-count heuristic (doesn't generalise across formats).

### 4. No truncation backstop for standalone zlib/deflate; containers use their CRC
Given the Investigation results, accept that a rapidgzip-accelerated standalone zlib/deflate
stream can miss a clean mid-stream truncation that stdlib would flag. For container members
(ZIP/7z) the shared verifying stage already checks the container CRC-32, which catches
truncation/corruption regardless of backend — so the container path is fully covered. Corruption
inside a DEFLATE block still raises and is translated to `CorruptionError`. Building a zlib
Adler-32 / decompressed-length backstop analogous to the gzip ISIZE check is possible but is
deferred to (and coordinated with) `rapidgzip-truncation-investigation` rather than bundled here.
**Rejected:** silently regressing truncation detection without recording it (done — spec states
the limitation); **Rejected:** forcing standalone zlib to stay on stdlib forever (the size
threshold plus container-CRC coverage make the accelerator worthwhile for large streams).

### 5. Slow-rewind diagnostics name the accelerator for zlib/deflate
Today the diagnostic spec says "zlib records no accelerator". With a rapidgzip path available,
the stdlib-fallback rewind for zlib/deflate names the `[seekable]` accelerator, consistent with
gzip, so the diagnostic tells users an accelerator would have avoided the O(n) rewind.

## Risks / Trade-offs

- **[Truncation regression for standalone zlib/deflate]** → documented in spec (Decision 4);
  container members are covered by CRC; a future backstop is scoped to the truncation change.
- **[Over-read on an unbounded slice]** → codec never adds an unbounded wrapper; the bounded-input
  requirement is normative and tested with a trailing-bytes fixture.
- **[Wrong/low threshold hurts either tiny or large members]** → the value is chosen from the
  benchmark below, not guessed; `ON`/`OFF` remain available as explicit overrides.
- **[Accelerator lifecycle / shutdown abort]** → reuse the existing `_AcceleratorStream`
  close-guard unchanged (same mechanism as gzip/bzip2).

## Open Questions

- **Threshold value.** Benchmark rapidgzip vs stdlib `zlib` decode+seek across compressed sizes
  (e.g. 1 KiB → 10 MiB) for raw deflate, zlib, and gzip, on Linux and macOS, measuring the
  crossover where rapidgzip's setup cost is repaid. Pick a single conservative `AUTO` threshold
  (likely one value across the family) and record it in design + user docs. Confirm the crossover
  isn't dominated by the many-small-members aggregate case (repeated per-stream setup).
- **Threshold location.** `AcceleratorMode.enabled_for(input_size=...)` vs codec-site check —
  decide during apply based on which keeps gzip/bzip2/deflate/zlib sharing one gate cleanly.
