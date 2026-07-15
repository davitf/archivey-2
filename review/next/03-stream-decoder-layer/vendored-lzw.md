# vendored-lzw.md — `unix_compress.py` hostile-input review

Hand-written LZW decode of untrusted `.Z` bytes in the **zero-dep trusted core**, with no
`[extra]` gate to hide behind, so VISION #2 applies at full strength.

## F3 (Med) — decompression bomb: eager per-`feed` decode + no `maxbits` ceiling

### 3a. `feed()` has no output budget; the base buffers the whole thing

`LzwState._process` (`unix_compress.py:143`) decodes **all** currently-buffered compressed
input into a single `output` bytearray and returns it. The base stream reads a 64 KB
compressed chunk and appends the *entire* decoded result to `self._buffer`:

```python
# decompressor_stream.py:302
if len(self._buffer) < n and not self._eof:
    self._buffer.extend(self._read_decompressed_chunk())   # extends by a full feed()'s output
```

So there is no back-pressure between "bytes the caller asked for" and "bytes produced". A
single `read(1)` forces one `_read_decompressed_chunk`, which for LZW can be arbitrarily
large because LZW's amplification is **unbounded** and *grows with stream position* (later
codes reference ever-longer dictionary entries) — unlike deflate (~1032:1 hard cap) or the
LZMA family, which archivey trusts to be roughly bounded per input chunk.

**Measured** (`repro/lzw_enc2.py`, a valid run-of-`'A'` stream the real `compress` would
also emit):

```
mw=16 codes=20000: input=33665B decoded=200030001B (x5942)
mw=31 codes=60000: input=112101B decoded=1800090001B (x16058)   # 112 KB -> 1.8 GB
read(1) on a 52 KB .Z: internal buffer now 450MB; peak alloc 1402MB
```

`read(1)` returning **one byte** leaves a **450 MB** buffer resident. The output is verified
to be a legitimate all-`'A'` run (correct roundtrip, not an encoder artifact).

### 3b. `maxbits` is bounded to 31, not 16

`_parse_header` (`unix_compress.py:51`):

```python
max_width = flag_byte & _CODE_WIDTH_FLAG      # _CODE_WIDTH_FLAG = 0x1F  -> up to 31
if max_width < _INITIAL_CODE_WIDTH:           # only the lower bound (9) is checked
    raise CorruptionError(...)
```

`repro/lzw_trunc.py` confirms `maxbits ∈ {17,24,31}` are all **accepted**. Real `compress`
/ `gzip -d` cap `maxbits` at 16 (`BITS`); anything above is rejected. The dictionary is
bounded only by `2**maxbits` entries (`next_code <= current_mask` guard,
`unix_compress.py:264`), so allowing 31 raises the ceiling from 2¹⁶ to 2³¹ and removes the
natural per-entry-length bound that 16 bits imposes — directly worsening 3a.

**This is inherited, not introduced:** upstream `uncompresspy` has the identical
`self._max_width = flag_byte & _CODE_WIDTH_FLAG` with only a `< 9` check
(`uncompresspy.py:117`). The port is faithful — but the missing cap is now in *archivey's*
trusted core, and the brief explicitly asks "is that field itself bounded?" The honest
answer is "to 31, not to the format's 16."

### Existing mitigations and their gaps

- `ArchiveyConfig` has a decompression-ratio guard (`config.py:84`, `max_ratio=1000`,
  `ratio_activation_threshold=5 MB`), but it only applies to `extract`/`extract_all`, and it
  counts bytes **after** each 1 MB copy chunk is already produced — the eager `feed()` in 3a
  balloons memory *inside* a single `read()` before the guard's counter advances. So the
  guard bounds total *extracted* bytes, not *peak* memory per read.
- `stream_members` / forward-only iteration enforce **no** caps at all (documented at
  `config.py:104`), so a caller streaming a `.Z` member has no protection whatsoever.

### Why it matters (VISION #2 / #4)

A ~50 KB attacker file forcing hundreds of MB of resident buffer on the first byte read is
a memory-safety problem in the trusted core, and it undercuts the "honest cost signals"
claim (the cost of `read(1)` is wildly non-local). Suggested direction in QUESTIONS.md:
cap `maxbits` at 16, and/or give the decoder an output budget (decode at most ~N bytes per
`feed`, retaining un-consumed compressed input) so the base's existing chunking actually
bounds peak memory.

## F4 (Med) — `.Z` truncation not raised on a single-shot `read(-1)`

`UnixCompressDecoder` reports truncation via the deferred `pending_error` mechanism: at EOF
`flush()` sets `self._pending_error = TruncatedError(...)` when leftover bits are nonzero
(`unix_compress.py:347`). The base only consults `pending_error` on a `read` that returns
**no** bytes (`decompressor_stream.py:307`):

```python
def read(self, n=-1):
    if n is None or n < 0:
        data = self.readall()      # returns ALL remaining bytes in one shot
    ...
    if not data:                   # <-- pending_error only checked here
        err = self._decoder.pending_error
        ...
```

`readall()` (`decompressor_stream.py:286`) never checks `pending_error` itself. So on a
truncated-but-partially-decodable `.Z`:

```
# repro/lzw_trunc2.py
read(-1) -> 6 bytes, no raise          # truncation NOT surfaced on this call
next read() -> raises TruncatedError    # only on the subsequent empty read
```

Contrast xz/lzip, whose `flush()` raises `TruncatedError` **directly** inside
`_read_decompressed_chunk`, so `read(-1)` raises on the *first* call
(`repro/xz_trunc.py`: `xz read(-1) raised on FIRST call: TruncatedError`).

A consumer that does one `stream.read()` and discards the handle — a very common idiom for
"read the whole member" — gets truncated data from a `.Z` with **no error**, while getting
a clean `TruncatedError` from every other codec. The `_copy_to_fileobj` extraction path
reads in a bounded loop to empty, so it *does* surface the error; the gap is single-shot
`read()`/`readall()`. This is a VISION #3 consistency bug (truncation is a first-class
citizen — but only for some codecs on some read shapes).

Fix sketch: have `readall()` check `pending_error` after the loop, or have unix-compress
raise from `flush()` directly like the other seekable decoders (the deferred mechanism buys
nothing here — the leftover-bits signal is already known at `flush` time).

## What is fine

- **KwKwK / index-past-table is bounded** (`unix_compress.py:244-259`): `dictionary[code]`
  only `IndexError`s into the `code == next_code` branch, which requires `prev_entry is not
  None` (raising `CorruptionError` otherwise); `code > next_code` is a clean
  `CorruptionError`. `len(dictionary) == next_code` is invariant, so no code indexes past
  the live table and no unbounded index growth beyond the `2**maxbits` cap.
- **CLEAR realignment / `pending_skip`** across feed boundaries (`unix_compress.py:234`) and
  **after-placement CLEAR seek points** (`unix_compress.py:371`) match the design.
- **Origin header commit** (`_commit_header_points` → `SeekPoint(0, 3)`,
  `unix_compress.py:358`) and its `recreate` reset (`unix_compress.py:320`, fresh
  `LzwState`, `pending_error` cleared by the base on reset) are correct.
- **Vendoring complete**: no runtime `import uncompresspy` in `src/`; BSD-3 notice intact
  (`unix_compress.py:412-443`).
