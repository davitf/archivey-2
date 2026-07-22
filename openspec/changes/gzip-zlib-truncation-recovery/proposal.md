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
  `pending_error` (same shape as unix-compress leftover bits). Chunked /
  bounded `read(n)` returns the prefix; the next empty `read` raises.
  `readall` / `read(-1)` raises (cannot return bytes and signal incompleteness).
  **`close()` never raises** decode `TruncatedError` / `CorruptionError`.
- **Size / seek integrity**: incomplete EOF MUST NOT publish a clean decompressed
  `_size`; `seek(SEEK_END)` / `try_get_size` must not treat a truncated prefix as
  a successful complete stream (raise pending truncation or leave size unknown).
  The gate applies at **both** writer sites (the EOF branch *and* `readall`'s
  post-loop `_size` assignment, which today runs before it raises) and is
  codec-agnostic: it also corrects truncated **unix-compress `.Z`**, which
  currently leaks a clean size because its decoder reports `finished` alongside
  the pending truncation.
- **`GzipCodec` stdlib backend**: stop using `gzip.GzipFile`; decode through a
  gzip-window decoder on `DecompressorStream` (`wbits=16+MAX_WBITS`) with
  **GzipFile-parity multi-member chaining** (zero-pad skip, trailing zeros =
  clean EOF, trailing non-gzip junk = `CorruptionError`).
- CRC/ISIZE outcomes stay equivalent to stdlib gzip (via zlib’s gzip window, not
  GzipFile’s manual trailer check).
- **Standing close contract (ADR 0014):** content/decode faults raise only from
  `read` (and size/seek paths that would otherwise lie); never from `close`. Align
  `VerifyingStream` / fused `MemberVerifier`:
  - **Size-declared** digest mismatch / over-run: raise on the reaching read and
    **withhold** that chunk.
  - **Size-unknown** digest mismatch: deliver data bytes; raise on the
    EOS-observing empty `read`.
  - **`read(-1)`**: raise as part of the complete-stream call so
    `read(); close()` cannot silently accept bad content (bounded drain loop;
    never `inner.read(-1)` on the sized bomb-cap branch).
  - **Full-count `read(n)`** on the public `ArchiveStream` / verifier surface so
    `read(member.size)` reaches the end.
  - **Seek:** forfeit checksum only; keep length / truncation / over-run.
- **Scope of the never-raise-on-close rule**: the standing rule applies to the
  `DecompressorStream` family and `VerifyingStream` / `MemberVerifier`; the
  rapidgzip accelerator and other wrappers are out of scope (they already signal
  content faults from `read`, not `close`).
- Rapidgzip / ISIZE backstops remain **out of scope** for accelerator behavior;
  once this lands, any empty→stdlib fallback can call this engine with large
  reads safely.

## Capabilities

### New Capabilities

<!-- none -->

### Modified Capabilities

- `compressed-streams` — deferred truncation delivery for the shared
  decompressor engine; never-raise-on-close for content errors; gzip stdlib
  backend via zlib/gzip-window decoder with multi-member support;
  recoverable-prefix contract for truncated streams; VerifyingStream close
  alignment.

## Impact

- Modules: `internal/streams/decompressor_stream.py`, `decompress.py`,
  `codecs.py` (`GzipCodec`), possibly a small `GzipDecoder` next to `ZlibDecoder`;
  `xz.py` / `lzip.py` (`flush` → `pending_error` for engine consistency);
  `verify.py` (`MemberVerifier.finish_on_close` / read-path EOF verdicts).
- Public API: same `open_codec_stream` / archive surfaces; stronger guarantee that
  truncated gzip/zlib/deflate/xz/lzip streams yield a correct prefix on `read(n)`
  before `TruncatedError`; `close()` stays teardown-only for content errors.
- Deps/extras: none (stdlib `zlib` only).
- Tests: truncated gzip/zlib/xz/lzip with large `read(n)`; multi-member +
  padding/junk; `SEEK_END` / size after truncate; VerifyingStream: size-declared
  mismatch withholds on reaching read; size-unknown delivers then empty raises;
  slurping `read(-1)` raises; anti-footgun `read(); close()`; full-count over
  short-reading inners; seek forfeits checksum but keeps length checks.
- Docs: `library-analysis.md` gzip row; truncation-vs-corruption best-effort
  verdict; note that stdlib path is no longer `GzipFile`.
