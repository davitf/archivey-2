# Compressed Streams — delta (zstd stdlib backend migration)

## MODIFIED Requirements

### Requirement: Each codec has a default backend

The system SHALL decompress each supported codec through a default backend. zstd's default
backend is the **standard-library `compression.zstd`** when importable (Python 3.14+),
otherwise **`backports.zstd`** (the same `compression.zstd` API, for Python 3.11–3.13), in
place of the previous `zstandard` backend. All other codec backends are unchanged (gzip →
stdlib `gzip`; bzip2 → stdlib `bz2`; xz → native xz over stdlib `lzma`; raw LZMA1/LZMA2 →
`lzma` `FORMAT_RAW`; Delta/BCJ → `lzma` raw filters; raw Deflate → `zlib`; Copy → pass-through;
lz4 → `lz4`; Brotli → `brotli`; unix-compress → `uncompresspy`; PPMd → `pyppmd`; Deflate64 →
`inflate64`; AES → the wrapped crypto backend).

#### Scenario: default zstd backend on Python 3.14+

- **WHEN** a zstd stream is opened with default configuration on Python 3.14 or newer
- **THEN** it is decompressed using the standard-library `compression.zstd` module

#### Scenario: default zstd backend on Python 3.11–3.13

- **WHEN** a zstd stream is opened with default configuration on Python 3.11–3.13 and `backports.zstd` is installed
- **THEN** it is decompressed using `backports.zstd` (the same `compression.zstd` API)

#### Scenario: raw LZMA2 backend for a 7z folder

- **WHEN** a 7z folder's LZMA2 stream is opened
- **THEN** it is decompressed using `lzma` in `FORMAT_RAW` mode

### Requirement: Returned streams translate decompression errors

The system SHALL wrap each backend stream so decompression failures surface as the library's
own exception types: corrupt data as `CorruptionError`, unexpected end-of-input as
`TruncatedError`, and a backend that requires seeking on a non-seekable source as the
documented non-seekable error. For the zstd backend specifically, the `compression.zstd`
`ZstdError` SHALL map to `CorruptionError` and its `EOFError` (raised on a truncated frame —
which the previous `zstandard` backend did **not** raise) SHALL map to `TruncatedError`. No raw
backend exception escapes unwrapped.

#### Scenario: truncated zstd data raises

- **WHEN** a zstd stream that ends before its end-of-frame marker is read to EOF
- **THEN** `TruncatedError` is raised (the stdlib backend reports the cut as `EOFError`), rather than a silent short read

#### Scenario: corrupt zstd data raises

- **WHEN** a zstd frame carrying a content checksum is corrupted and read
- **THEN** `CorruptionError` is raised with the backend `ZstdError` attached as `__cause__`
