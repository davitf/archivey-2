# Tasks — gzip via zlib DecompressorStream + recoverable truncation

## 1. DecompressorStream truncate-return semantics

- [ ] 1.1 Change `_read_decompressed_chunk` incomplete-EOF path: return flush
      leftover and set `pending_error=TruncatedError` instead of raising inline
      (do not drop bytes already in `_buffer`).
- [ ] 1.2 Make `readall` / `read(-1)` return the recoverable prefix while leaving
      `pending_error` set (stop dropping prefix to raise immediately).
- [ ] 1.3 Raise `pending_error` from `close` after closing the inner (and keep
      raise-on-next-empty-`read`); clear via `clear_pending_error` after raise /
      seek reset.
- [ ] 1.4 Update unix-compress truncation tests for the new `readall` /
      close-raises contract; keep chunked “prefix then next empty read raises”
      green.

## 2. GzipDecoder + GzipCodec stdlib path

- [ ] 2.1 Add a gzip-window decoder (`wbits=16+MAX_WBITS`) that chains
      concatenated members via `unused_data` (GzipFile multi-member parity).
- [ ] 2.2 Wire `GzipCodec.open` (non-accelerator path) to
      `DecompressorStream` + that decoder; remove `gzip.open` / `GzipFile` as
      the decode engine. Keep exception translation equivalent (`zlib.error` /
      truncation → `CorruptionError` / `TruncatedError`).
- [ ] 2.3 Confirm CRC/trailer failures still map to `CorruptionError` (zlib gzip
      window); intact single- and multi-member files match `gzip.GzipFile`
      oracle output.
- [ ] 2.4 Ensure `tell` / `seek` (when seekable) track engine `_pos` correctly on
      the new path; rewind warning unchanged when accelerator off.

## 3. Truncation recovery tests

- [ ] 3.1 Truncated single-member gzip: assert large `read(n)` / `read(-1)`
      recover the same correct prefix as a `read(1)` loop, then `TruncatedError`
      on next empty read or `close` (oracle = GzipFile `read(1)` loop max).
- [ ] 3.2 Same for truncated raw deflate / zlib through `ZlibDecompressorStream`
      (engine-level; proves 1.x is not gzip-only).
- [ ] 3.3 Multi-member intact gzip still fully concatenates; truncated
      mid-second-member delivers prefix through first member + partial second
      then truncates loudly.
- [ ] 3.4 Valid empty gzip and valid empty-payload member still succeed with
      zero bytes.

## 4. Docs + compose notes

- [ ] 4.1 Update `docs/internal/library-analysis.md` gzip row: stdlib path is
      gzip-window `DecompressorStream`, not `GzipFile`.
- [ ] 4.2 Note in `docs/internal/open-issues.md` (or the rapidgzip change) that
      empty→stdlib fallback SHOULD use this engine so `_STDLIB_READ_SIZE = 1` is
      unnecessary — do not implement the rapidgzip fallback switch in this
      change unless that code is already present and trivial to retarget.

## 5. Verify

- [ ] 5.1 Targeted pytest for §§1–3 (`test_codecs`, accelerator-off gzip,
      zlib/deflate truncation).
- [ ] 5.2 `uv run --no-sync ruff check` / `ruff format` on touched paths;
      `pyrefly check` + `ty check` clean.
- [ ] 5.3 `openspec validate --strict gzip-zlib-truncation-recovery`
