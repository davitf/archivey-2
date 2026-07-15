# Reproducers for Brief 3 findings

Run from this directory with the repo env active (`uv run python <script>`).
Backends used: rapidgzip 0.16.0 (accel_*), stdlib lzma/zlib (xz_*, lzw_*).

- `xz_craft.py`   — builds `evil_zero_blocks.xz` (F1 fixture) + prints the backward-scan blocks
- `xz_crash.py`   — F1: `seek(0, SEEK_END)` → AssertionError (run xz_craft.py first)
- `xz_size.py`    — F1: `try_get_size()` → AssertionError
- `xz_trunc.py`   — F4 contrast: xz raises TruncatedError on the first `read(-1)`
- `lzw_enc2.py`   — F3: LZW amplification + `read(1)` buffer blowup (52 KB → 450 MB)
- `lzw_trunc.py`  — F3b: `maxbits` 17/24/31 accepted; `read(-1)` swallow probe
- `lzw_trunc2.py` — F4: `.Z` truncation not raised on first `read(-1)`
- `accel_valid.py`   — F2: valid vs truncated deflate/zlib through rapidgzip
- `accel_trunc.py`   — F2: stdlib raises vs rapidgzip swallows (truncation)
- `accel_corrupt.py` — F2: mid-stream corruption swallowed by accelerated deflate/zlib
- `accel_gzip.py`    — F2: gzip ISIZE backstop works on path, skipped on BytesIO
