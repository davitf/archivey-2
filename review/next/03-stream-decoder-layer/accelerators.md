# accelerators.md — `codecs.py` rapidgzip hot-path review

Context: #105 put rapidgzip on **deflate/zlib** (the commonest codecs) behind the AUTO
size gate, where it was previously gzip/bzip2 only.

## F2 (High) — accelerated deflate/zlib silently swallow truncation *and* corruption

`DeflateCodec.open` (`codecs.py:953`) and `ZlibCodec.open` (`codecs.py:987`) route through
`_open_rapidgzip(source)` — a bare `_AcceleratorStream` with **no truncation backstop**.
The only backstop, `_GzipTruncationCheckStream`, is applied by `GzipCodec.open`
(`codecs.py:606`) and *only* for a seekable **path** source. deflate and zlib get nothing,
and they have no length trailer for a backstop to check even in principle.

### Measured (`repro/accel_valid.py`, `repro/accel_trunc.py`, `repro/accel_corrupt.py`)

Valid input decodes correctly through rapidgzip (byte-identical to the payload), so there
is **no correctness cliff for valid data** at the AUTO threshold. The problem is damaged
input:

| Codec | Input | stdlib (`use_rapidgzip=OFF`) | rapidgzip (`ON`) |
|-------|-------|------------------------------|------------------|
| raw-deflate | truncated to 66%/90% | `TruncatedError` | **0 bytes, no error** |
| zlib | truncated to 66%/90% | `TruncatedError` | **0 bytes, no error** |
| raw-deflate | mid-stream corruption | (stdlib raises) | **96776/105000 bytes, no error** |
| zlib | mid-stream corruption | (stdlib raises) | **96776/105000 bytes, no error** |
| gzip | truncated, **path** source | `TruncatedError` | `TruncatedError` (ISIZE backstop ✓) |
| gzip | truncated, **BytesIO** source | `TruncatedError` | **0 bytes, no error** (backstop skipped) |

So through the accelerator, a truncated deflate/zlib stream yields **zero** bytes and no
exception; a *corrupt* one yields a truncated prefix and no exception. The stdlib
`DecompressorStream` path raises `TruncatedError` (its `_read_decompressed_chunk` checks
`if not self._decoder.finished`, `decompressor_stream.py:279`) and delivers the recoverable
prefix on a bounded read.

### Activation surface

`_rapidgzip_enabled` (`codecs.py:232`) → `AcceleratorMode.enabled_for`
(`config.py:36`): the accelerator engages when `use_rapidgzip=ON`, **or** `AUTO` +
`config.seekable` + known `compressed_input_size ≥ 1 MiB`
(`RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE`, `config.py:73`). Public `open_stream` defaults to
`seekable=False`, so the *default* single-stream open stays on stdlib — but any caller that
requests seekable/random-access streams over a ≥1 MB deflate/zlib member (e.g. seekable ZIP
member access, or `use_rapidgzip=ON`) lands on the silent path.

For ZIP deflate members, container-level CRC verification (`verify.py`) may catch the
mismatch downstream *when enabled* — but that turns the bug into an all-or-nothing error and
still **loses the recoverable prefix** that VISION #3 promises. For standalone `.zlib`
single-file streams, and any path where digest verification is off, the truncation/corruption
is simply invisible.

### Why it matters (VISION #3)

"Damaged input is a first-class citizen — truncation → recoverable members + honest error."
The accelerated deflate/zlib path delivers neither: no honest error, and (for truncation)
not even the recoverable prefix. It is strictly worse than the stdlib path it silently
replaces once a member crosses 1 MB in seekable mode.

### Fix direction (QUESTIONS.md)

deflate/zlib can't grow a length trailer, but the decode *completeness* is knowable: after
a full sequential read, rapidgzip's own end-of-stream state (or a stdlib re-check of the
tail) could distinguish "clean EOS" from "ran out". At minimum, a rapidgzip stream that
returns 0 bytes for a non-empty compressed input, or stops before EOS, should raise rather
than report clean EOF. Alternatively, gate deflate/zlib acceleration behind the same
"seekable path with a verifiable structure" condition the gzip backstop already requires.

## Lifecycle, free-threading, error tables (checked, no new finding)

- **Finalizer** (`_AcceleratorStream`, `codecs.py:113-155`): correct and at the birth site.
  `weakref.finalize(self, self._close_inner, self._inner)` uses a `staticmethod` that closes
  the raw inner (no `self` capture that would pin the wrapper), holding a strong ref so
  `close()` runs before the object is freed; `close()` just fires the guard early. A
  million-member sweep that never explicitly closes still relies on GC to run the finalizer
  (threads/FDs live until collection), but that is the documented design, not a regression,
  and it is the same for gzip/bzip2 as before #105.
- **Free-threading:** the accelerators remain single-object, GIL-serialized here (one live
  rapidgzip object per stream, `parallelization=0`). #105 did not add cross-stream sharing,
  so the "GIL-only, don't promise otherwise" boundary is unchanged. Not independently
  stress-tested under `3.13t` in this pass — flagged only as "unchanged", not "verified
  safe".
- **Error-message tables vs the pinned floor:** on the installed `rapidgzip 0.16.0`
  (`[all-lowest]` floor), the cases that *do* raise are still matched — corrupt gzip surfaces
  via the ISIZE backstop, and `_translate_rapidgzip` (`codecs.py:259`) maps the
  header/format/`std::exception` cases. The F2 gap is orthogonal: deflate/zlib
  truncation/corruption produces **no exception for the table to translate**, so the tables
  being correct doesn't help.
- **bzip2** (`_rapidgzip_bzip2` / `IndexedBzip2File`, `codecs.py:110`): out of the deflate/zlib
  scope of #105; its `_translate_accelerator` table (`codecs.py:727`) is unchanged and its
  CRC-per-block checks mean truncation/corruption there *does* raise (bzip2 has block CRCs;
  deflate/zlib do not). Not a finding.
