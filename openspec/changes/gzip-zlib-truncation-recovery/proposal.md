## Why

CPython `gzip.GzipFile` (and today’s `DecompressorStream` truncate path) can raise
`TruncatedError`/`EOFError` on a large `read(n)` while discarding a correct
recoverable prefix — or leave that prefix stranded in an internal buffer the
caller never sees. Raw `zlib.decompressobj` with the gzip window recovers the
full prefix on large feeds; archivey’s zlib engine can too once truncate-return
semantics match the existing `pending_error` pattern. Switching stdlib gzip onto
that path removes the byte-at-a-time workaround class of bugs and unifies
DEFLATE-family decode.

## What Changes

- **`DecompressorStream`**: on incomplete EOF, deliver flush/buffer leftover
  (and any already-buffered output) before surfacing `TruncatedError` via
  `pending_error` (same shape as unix-compress leftover bits) — including
  `read(n)`, and a decided `readall`/`close` rule so prefixes are not dropped.
- **`GzipCodec` stdlib backend**: stop using `gzip.GzipFile`; decode through
  `ZlibDecompressorStream` / a gzip-capable decoder (`wbits=16+MAX_WBITS`) with
  **multi-member chaining** (GzipFile parity — today’s bare zlib stream stops
  after the first member).
- Exception translation and CRC/trailer checks stay equivalent to stdlib gzip.
- Rapidgzip / ISIZE backstops are **out of scope** here (separate change); once
  this lands, any empty→stdlib fallback can call the same engine with large
  reads safely.

## Capabilities

### New Capabilities

<!-- none -->

### Modified Capabilities

- `compressed-streams` — deferred truncation delivery for the shared
  decompressor engine; gzip stdlib backend via zlib/gzip-window decoder with
  multi-member support; recoverable-prefix contract for truncated streams.

## Impact

- Modules: `internal/streams/decompressor_stream.py`, `decompress.py`,
  `codecs.py` (`GzipCodec`), possibly a small `GzipDecoder` next to `ZlibDecoder`.
- Public API: same `open_codec_stream` / archive surfaces; stronger guarantee that
  truncated gzip/zlib/deflate streams yield a correct prefix before
  `TruncatedError`.
- Deps/extras: none (stdlib `zlib` only).
- Tests: truncated gzip/zlib with large `read(n)` and `read(-1)`; multi-member
  gzip parity; CRC mismatch; compare against `gzip.GzipFile` oracle where useful.
- Docs: `library-analysis.md` gzip row; note that stdlib path is no longer
  `GzipFile`.
