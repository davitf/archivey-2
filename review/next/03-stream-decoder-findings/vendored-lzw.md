# Vendored LZW & the deferred-truncation path (F3, F4)

`internal/streams/unix_compress.py` is hand-written LZW decompression of untrusted `.Z`
bytes in the **zero-dep core** — VISION #2's "memory-safe hostile-input parsing" applies
with no `[extra]` shield. The LZW kernel's decode logic is careful (KwKwK, code bounds,
provenance; see "what's actually fine" in `SUMMARY.md`), but the *shape* of how it feeds
the base stream, and one missing header bound, make a decompression bomb; and a truncated
`.Z` is silently accepted on the single-shot read idiom.

## F3 (Medium) — decompression bomb: eager per-`feed` decode + no `maxbits` ceiling

### F3a — no per-`feed` output budget; the base buffers the whole result (all feed codecs)

> **Scope corrected after maintainer review (2026-07-16).** This is **not** LZW-specific.
> The root cause is in the base: `_read_decompressed_chunk` reads a 64 KB *compressed*
> chunk and appends the **entire** decoded result to `self._buffer`, with no back-pressure
> between "bytes the caller asked for" and "bytes produced". Every `DecompressorStream`-based
> codec inherits it. LZW is the sharpest instance (below), not the only one.

```
# decompressor_stream.py:302  read
if len(self._buffer) < n and not self._eof:
    self._buffer.extend(self._read_decompressed_chunk())   # extends by a full feed()'s output
```

`LzwState._process` (`unix_compress.py:143`) decodes **all** currently-buffered compressed
input into one `output` bytearray, so the base buffers it whole. But so do the other
feed-based decoders — re-measured, each decoding a 50 MB all-`'A'` payload with `read(1)`
(asking for one byte):

| codec (via `DecompressorStream`) | compressed input | buffer after `read(1)` |
| --- | --- | --- |
| brotli | **80 B** | 50 MB |
| xz / lzip / lzma-alone / raw-LZMA | 7.4 KB | 50 MB |
| deflate / zlib (stdlib path) | 48.6 KB | 50 MB |
| unix-compress (LZW) | 9.4 KB | 20 MB |

(The stdlib *file-object* codecs — gzip, bz2, lz4, zstd — are **not** affected: they read
incrementally, `read(1)` peaks ≤ 0.1 MB. `LZMAFile` peaks ~8 MB, already far below the
`DecompressorStream` path.)

**Why LZW is still the headline of this theme file.** It is in the **zero-dep core** with
no `[extra]` gate to hide behind (VISION #2 at full strength), and its amplification is
**unbounded and grows with stream position** (later codes reference ever-longer dictionary
entries) — deflate has a ~1032:1 per-chunk cap; brotli/LZMA are higher but the codecs are
optional deps. A ~9 KB `.Z` forces ~20 MB of resident buffer on the **first byte read**,
scaling with payload length (a 50 KB `.Z` reaches hundreds of MB; PR #121 measured peak
~1.4 GB). The output is a legitimate all-`'A'` run (verified roundtrip, not an encoder
artifact).

The fix belongs in the base (an output budget on `Decoder.feed` + retained un-consumed
input); the `max_length`-capable backends (zlib, lzma, bz2, pyppmd) and our own LZW can all
honor it, brotli/deflate64 are the residual. Per-backend feasibility is worked out in
`QUESTIONS.md` Q3.

Existing mitigations do not cover this:
- The `ArchiveyConfig` decompression-ratio guard (`config.py:84`, `max_ratio=1000`,
  `ratio_activation_threshold=5 MB`) only applies to `extract`/`extract_all`, and counts
  bytes **after** each copy chunk is produced — the eager `feed()` balloons memory *inside*
  a single `read()` before the guard's counter advances. It bounds total *extracted* bytes,
  not *peak* memory per read.
- `stream_members` / forward-only iteration enforce **no** caps at all (`config.py:104`).

### F3b — `maxbits` bounded to 31, not the format's 16

```
# unix_compress.py:51
max_width = flag_byte & _CODE_WIDTH_FLAG       # _CODE_WIDTH_FLAG = 0x1F  -> up to 31
if max_width < _INITIAL_CODE_WIDTH:            # only the LOWER bound (>= 9) is checked
    raise CorruptionError(...)
```

The `.Z` format and every real `compress`/`ncompress` cap `maxbits` at 16; 17–31 are
invalid and rejected. archivey masks with `0x1F` and checks only the lower bound, so it
accepts `maxbits` up to 31 (`repro.py` F4/maxbits section confirms 17/24/31 all accepted).
The dictionary is bounded only by `2**maxbits` entries (`next_code <= current_mask`,
`unix_compress.py:264`), so allowing 31 raises the ceiling from 2¹⁶ to 2³¹ and removes the
natural per-entry-length bound 16 bits imposes — directly worsening F3a.

**This is inherited, not introduced:** upstream `uncompresspy` has the identical mask with
only a `< 9` check. The port is faithful — but the missing cap is now in archivey's trusted
core, and the brief explicitly asks "is that field itself bounded?" The honest answer is
"to 31, not to the format's 16."

### Why it matters (VISION #2 / #4)

A ~9 KB file forcing tens of MB (and, scaled up, hundreds of MB to > 1 GB) of resident
buffer on the first byte read is a memory-safety problem in the trusted core, and the cost
of `read(1)` is wildly non-local — undercutting the "honest cost signals" claim.
Suggested direction (QUESTIONS Q3): cap `maxbits` at 16, **and** give the decoder an output
budget (decode at most ~N bytes per `feed`, retaining un-consumed compressed input) so the
base's existing chunking actually bounds peak memory.

## F4 (Medium) — `.Z` truncation not raised on a single-shot `read(-1)`/`readall()`

### Mechanism

Unlike xz/lzip, the LZW decoder reports `finished == True` at flush even when truncated,
signalling truncation out-of-band via `pending_error`:

```
# unix_compress.py:343  UnixCompressDecoder.flush
if self._state.truncated:
    self._pending_error = TruncatedError("unix-compress (.Z) stream is truncated ...")
...
@property
def finished(self) -> bool:
    return self._state.is_finished()      # True after flush, truncated or not
```

Because `finished` is `True`, the base's eager truncation check (`decompressor_stream.py:279`,
`if not self._decoder.finished: raise`) does not fire, and the deferred error is only raised
on the **next empty `read`**:

```
# decompressor_stream.py:296  read
def read(self, n: int = -1, /) -> bytes:
    if n is None or n < 0:
        data = self.readall()             # readall never checks pending_error
    else:
        ...
    if not data:                          # only reached when read RETURNS EMPTY
        err = self._decoder.pending_error
        if err is not None:
            self._decoder.clear_pending_error(); raise err
    return data
```

`readall()` (`decompressor_stream.py:286`) loops to EOF and returns all bytes without ever
consulting `pending_error`. So the `read(-1)` idiom — `data = f.read()`, the most common
way to read a member — returns the partial data with the `TruncatedError` unraised; it only
surfaces if the caller reads *again* and gets empty.

### Reproduction (real `.Z` via `ncompress`)

```
uv run python review/next/03-stream-decoder-findings/repro.py     # F3/F4 section
  real .Z read(-1): 3975 bytes, NO error (SWALLOWED)
  real .Z chunked: TruncatedError raised (inconsistent with above)
```

The *same* truncated `.Z` raises `TruncatedError` when read in chunks but silently returns
partial data with `read(-1)`. `xz`/`lzip` raise on the first `read(-1)` (their `flush()`
raises directly inside `_read_decompressed_chunk`), so this is a per-codec, per-read-shape
inconsistency — a VISION #3 violation. The `_copy_to_fileobj` extraction path reads in a
bounded loop to empty, so it *does* surface the error; the exposed gap is single-shot
`read()`/`readall()`.

The LZW *undetectable* truncations (a cut landing on a code boundary with only zero leftover
bits) are **not** a finding — the code comments acknowledge them (`unix_compress.py:111-117`),
and `.Z` has no length trailer. F4 is strictly about detected truncations being dropped by
`read(-1)`.

### Fix sketch

Have `readall()`/the `read(-1)` branch check `pending_error` after the final chunk
(delivering bytes first, then raising on the terminal state — preserving the "bytes before
error" ordering), or have unix-compress raise from `flush()` directly like the other
seekable decoders (the deferred mechanism buys nothing here — the leftover-bits signal is
known at flush time).
