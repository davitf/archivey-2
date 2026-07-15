# Vendored LZW & the deferred-truncation path (F3, F4)

`internal/streams/unix_compress.py` is hand-written LZW decompression of untrusted `.Z`
bytes in the **zero-dep core** — VISION #2's "memory-safe hostile-input parsing" applies
with no `[extra]` shield. The kernel itself holds up well (see "what's actually fine" in
`SUMMARY.md`); the two findings are a base-class truncation swallow that only `.Z`
exercises, and a missing upper bound on the `maxbits` header field.

## F3 (Medium) — `read(-1)`/`readall()` swallow the deferred `TruncatedError`

### Mechanism

Unlike xz/lzip, the LZW decoder reports `finished == True` at flush even when the stream
was truncated, and signals truncation out-of-band via `pending_error`:

```
# unix_compress.py:343  UnixCompressDecoder.flush
def flush(self) -> DecodeOut:
    data, units = self._state.flush()
    ...
    if self._state.truncated:
        self._pending_error = TruncatedError(
            "unix-compress (.Z) stream is truncated (nonzero leftover bits ...)")
    return DecodeOut(data, points)

@property
def finished(self) -> bool:
    return self._state.is_finished()      # True after flush, truncated or not
```

Because `finished` is `True`, the base's eager truncation check does **not** fire:

```
# decompressor_stream.py:274  _read_decompressed_chunk
leftover = self._ingest_decode(self._decoder.flush())
if not self._decoder.finished:            # False for .Z -> no raise here
    raise TruncatedError("File is truncated")
```

The deferred error is only raised on the **next empty `read`**:

```
# decompressor_stream.py:296  read
def read(self, n: int = -1, /) -> bytes:
    if n is None or n < 0:
        data = self.readall()             # <-- readall never checks pending_error
    else:
        ...
    if not data:                          # only reached when read RETURNS EMPTY
        err = self._decoder.pending_error
        if err is not None:
            self._decoder.clear_pending_error()
            raise err
    return data
```

`readall()` (`decompressor_stream.py:286`) loops until EOF and returns all bytes; it
never consults `pending_error`. So the `read(-1)` idiom — `data = f.read()`, the single
most common way to read a stream — returns the partial data with the `pending_error`
still sitting unraised. The error only surfaces if the caller reads *again* and gets an
empty result, which many callers never do.

### Reproduction (real `.Z` via `ncompress`)

```
uv run python review/next/03-stream-decoder-findings/repro.py     # F3 section
```

```
real .Z read(-1): 3975 bytes, NO error (SWALLOWED)
real .Z chunked: TruncatedError raised (inconsistent with above)
```

The *same* truncated `.Z` raises `TruncatedError` when read in 256-byte chunks but
silently returns partial data when read with `read(-1)`. (The `repro.py` stand-in
decoder shows the identical base-class behaviour when `ncompress` is not installed.)

This is a VISION #3 violation ("damaged input is a first-class citizen → honest error")
and a read-style inconsistency: whether a truncation is reported depends on *how* the
caller reads, not on the data.

### Note on scope

Only `UnixCompressDecoder` uses the deferred `pending_error` path, so only `.Z` is
affected today. But the defect is in the **base** (`readall`/`read(-1)` ignoring
`pending_error`), so any future decoder that adopts the deferred-error contract inherits
it. The narrow fix is to check `pending_error` at the end of `readall`/the `read(-1)`
branch after the final chunk (delivering bytes first, then raising on the terminal
empty state — preserving the "bytes before error" ordering VISION #3 also requires).

The LZW-specific *undetectable* truncations (a cut landing on a code boundary with only
zero leftover bits) are **not** a finding — the code comments acknowledge them
(`unix_compress.py:111-117`, `:349`), and `.Z` has no length trailer to check against.
F3 is strictly about the truncations the decoder *does* detect being dropped by
`read(-1)`.

## F4 (Low-Medium) — `.Z` `maxbits` not bounded to the format ceiling of 16

```
# unix_compress.py:44
flag_byte = header[2]
...
max_width = flag_byte & _CODE_WIDTH_FLAG       # _CODE_WIDTH_FLAG = 0x1F  -> up to 31
if max_width < _INITIAL_CODE_WIDTH:            # only a LOWER bound (>= 9) is checked
    raise CorruptionError(...)
```

The `.Z` format (and every real `compress`/`ncompress`) restricts `maxbits` to 9–16;
values 17–31 are invalid and rejected by standard tools. archivey masks with `0x1F` and
checks only the lower bound, so it accepts `maxbits` up to 31:

```
uv run python review/next/03-stream-decoder-findings/repro.py     # F4 section
  maxbits=16: accepted max_width=16
  maxbits=17: accepted max_width=17  <-- out of spec, accepted
  maxbits=24: accepted max_width=24  <-- out of spec, accepted
  maxbits=31: accepted max_width=31  <-- out of spec, accepted
```

Consequence: the code table may grow to `2**maxbits` entries — up to 2³¹ with a crafted
header, versus the standard 2¹⁶ ceiling. It is **not** a small-file OOM bomb: dictionary
growth is bounded by the number of codes fed, so memory stays proportional to input
size. The concern is (a) accepting files every other `.Z` tool rejects (a compatibility
/ "honest parsing" gap — archivey may "successfully" decode something that is not a
valid `.Z`), and (b) permitting a larger-than-standard resident code table. Recommend
clamping to 16 (raise `CorruptionError` for `maxbits > 16`, matching the ecosystem) —
see QUESTIONS Q3.
