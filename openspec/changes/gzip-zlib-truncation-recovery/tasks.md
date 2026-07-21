# Tasks — gzip via zlib DecompressorStream + recoverable truncation

## 1. DecompressorStream truncate-return + size integrity

- [ ] 1.1 Change `_read_decompressed_chunk` incomplete-EOF path: return flush
      leftover and set `pending_error=TruncatedError` instead of raising inline
      (do not drop bytes already in `_buffer`). Do **not** publish a clean
      complete `_size` on incomplete EOF.
- [ ] 1.2 Bounded `read(n)` returns the recoverable prefix while leaving
      `pending_error` set; raise on the next empty `read`; clear via
      `clear_pending_error` after raise / seek reset.
- [ ] 1.3 Keep `readall` / `read(-1)` raising when `pending_error` is set after
      drain (no prefix returned from that call) — same shape as today’s
      unix-compress `readall` test.
- [ ] 1.4 `close()` MUST NOT raise `pending_error` (teardown-only for content
      faults).
- [ ] 1.5 Fix `seek(SEEK_END)` / `try_get_size` after incomplete EOF: raise
      pending truncation or leave size unknown — never `AssertionError`, never
      silent prefix-as-complete.
- [ ] 1.6 Confirm unix-compress chunked “prefix then next empty read raises”
      stays green; `readall` still raises; `close` after observed truncation is
      quiet.

## 2. GzipDecoder + GzipCodec stdlib path

- [ ] 2.1 Add a gzip-window decoder (`wbits=16+MAX_WBITS`) that chains members
      with **GzipFile parity**: on member complete, strip leading NULs from
      `unused_data` / retained input; empty → finished; `1f 8b` → new
      `decompressobj` and continue inside `feed`; anything else →
      `CorruptionError`. (Do not use a bare magic peek without zero-skip/junk
      handling.)
- [ ] 2.2 Wire `GzipCodec.open` (non-accelerator path) to
      `DecompressorStream` + that decoder; remove `gzip.open` / `GzipFile` as
      the decode engine. Keep exception translation equivalent (`zlib.error` →
      `CorruptionError`; engine `TruncatedError` passes through).
- [ ] 2.3 Confirm CRC/ISIZE failures still map to `CorruptionError` (zlib gzip
      window outcomes); intact single- and multi-member files match
      `gzip.GzipFile` oracle output.
- [ ] 2.4 Ensure `tell` / `seek` (when seekable) track engine `_pos` correctly on
      the new path; rewind warning unchanged when accelerator off.

## 3. Truncation recovery + multi-member tests

- [ ] 3.1 Truncated single-member gzip: assert large `read(n)` recovers the same
      correct prefix as a `read(1)` loop, then `TruncatedError` on next empty
      `read` (oracle = GzipFile `read(1)` loop max). `readall` raises.
      `close()` after the error was observed on `read` succeeds.
- [ ] 3.2 Same engine-level coverage for truncated raw deflate / zlib through
      `ZlibDecompressorStream` (proves 1.x is not gzip-only).
- [ ] 3.3 Multi-member intact gzip fully concatenates; zero-padded multi-member
      concatenates; trailing zeros only → clean EOF; trailing junk →
      `CorruptionError`; truncated mid-second-member delivers prefix through
      first member + partial second then truncates loudly on the read path.
- [ ] 3.4 Valid empty gzip and valid empty-payload member still succeed with
      zero bytes.
- [ ] 3.5 After truncated decode, `seek(SEEK_END)` / size APIs do not report a
      clean complete prefix size (raise or unknown per 1.5).
- [ ] 3.6 Update existing gzip truncate tests that assume `read()`/`readall`
      shapes under the new stdlib backend (e.g. `test_truncated_gzip_translates_to_truncated`).

## 4. VerifyingStream / MemberVerifier — CRC after all data

- [ ] 4.1 Digest/CRC mismatch: return every decompressed byte on data-returning
      reads; at clean EOF raise `CorruptionError` on the **next** (terminal
      empty) `read`. Do not withhold the last data chunk; do not raise from
      `finish_on_close`.
- [ ] 4.2 After a slurping `read()` / `read(-1)` that returned the full body,
      arm the digest/short verdict so the following empty `read` raises (same
      timing as chunked). Hash-less short → `TruncatedError` on that empty
      `read`.
- [ ] 4.3 `finish_on_close` closes the inner and MUST NOT introduce a first
      content `TruncatedError` / `CorruptionError` solely on close (may still
      avoid double-fault after `read` already failed).
- [ ] 4.4 Update verify tests: keep
      `test_verify_mismatch_raises_at_eof_without_losing_final_chunk`; change
      close-raises cases (`test_verify_on_close_after_full_single_read`,
      `test_verify_expected_size_short_raises_truncated`, hashed short, etc.)
      to “full body returned, next empty `read` raises, `close` quiet”; keep
      “partial read then close is ok”.

## 5. Docs + compose notes

- [ ] 5.1 Update `docs/internal/library-analysis.md` gzip row: stdlib path is
      gzip-window `DecompressorStream`, not `GzipFile`.
- [ ] 5.2 Note in `docs/internal/open-issues.md` (or the rapidgzip change) that
      empty→stdlib fallback SHOULD use this engine so a byte-at-a-time
      workaround is unnecessary — do not implement the rapidgzip fallback
      switch in this change unless that code is already present and trivial to
      retarget. (`_STDLIB_READ_SIZE = 1` is not in tree today.)

## 6. Verify

- [ ] 6.1 Targeted pytest for §§1–4 (`test_codecs`, accelerator-off gzip,
      zlib/deflate truncation, verify close/read contracts).
- [ ] 6.2 `uv run --no-sync ruff check` / `ruff format` on touched paths;
      `pyrefly check` + `ty check` clean.
- [ ] 6.3 `openspec validate --strict gzip-zlib-truncation-recovery`
