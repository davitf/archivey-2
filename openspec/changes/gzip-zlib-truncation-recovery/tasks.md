# Tasks — gzip via zlib DecompressorStream + recoverable truncation

## 1. DecompressorStream truncate-return + size integrity

- [ ] 1.1 Change `_read_decompressed_chunk` incomplete-EOF path: return flush
      leftover and surface `TruncatedError` via `pending_error` instead of raising
      inline (do not drop bytes already in `_buffer`). Per resolved Decision 1 the
      **decoder** owns detection: its `flush` sets its own `pending_error` on
      `not finished`, and the stream drops the `not finished` raise and checks
      `self._decoder.pending_error`. Document that `flush` responsibility on the
      `Decoder` protocol + `BaseDecoder` (it is the once-at-EOF truncation-detection
      point); optionally rename `flush` → `finish`/`finalize` (touches every
      decoder — implementor's call). Gate the `_size` assignment on "not truncated"
      (`pending_error is
      None` **and** `finished`) at **both** writer sites — the EOF branch **and**
      `readall`'s post-loop `self._size = self._pos` (which currently runs before
      it raises the deferred error). Cover the size-driven forward-only decoders
      (BCJ / PPMd / Deflate64), not just zlib/gzip.
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
- [ ] 1.7 **Behavior change (not "stays green"):** a truncated `.Z` currently
      publishes a clean complete `_size` (flush reports `finished=True` +
      `pending_error`), so `try_get_size` / `seek(SEEK_END)` report the prefix as
      complete. Assert (red→green) that after the size-gate fix a truncated `.Z`
      reports size unknown or raises `TruncatedError`, never the prefix length.

## 2. GzipDecoder + GzipCodec stdlib path

- [ ] 2.1 Add a gzip-window decoder (`wbits=16+MAX_WBITS`) that chains members
      with **GzipFile parity**: on member complete, strip leading NULs from
      `unused_data` / retained input; empty → finished; `1f 8b` → new
      `decompressobj` and continue inside `feed`; anything else →
      `CorruptionError`. (Do not use a bare magic peek without zero-skip/junk
      handling.) Because `feed` is incremental (no peek/seek on `inner`), handle
      the cross-`feed` cases explicitly: a NUL run split across chunks must be
      buffered, not read as EOF or junk until the next header (or true EOF) is
      seen; a lone trailing partial magic (e.g. `1f`) must be retained and
      resolved at `flush`, not decided eagerly. Implement `needs_input` so it
      stays **false** while retained post-member `unused_data` remains to drain,
      and integrate with `unconsumed_tail` under `max_length` (same shape as
      `ZlibDecoder`).
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
      Cover the cross-`feed` edges (feed the compressed bytes in small chunks so
      boundaries land mid-run): NUL padding split across a feed boundary still
      concatenates; a member boundary read one byte at a time loses no bytes; a
      lone trailing `1f` at EOF raises `CorruptionError` (not a dropped member).
- [ ] 3.4 Valid empty gzip and valid empty-payload member still succeed with
      zero bytes.
- [ ] 3.5 After truncated decode, `seek(SEEK_END)` / size APIs do not report a
      clean complete prefix size (raise or unknown per 1.5).
- [ ] 3.6 Update existing gzip truncate tests that assume `read()`/`readall`
      shapes under the new stdlib backend (e.g. `test_truncated_gzip_translates_to_truncated`).

## 4. VerifyingStream / MemberVerifier — CRC after all chunked data

- [ ] 4.1 Bounded `read(n)` digest/CRC mismatch **and hash-less short**: return
      every decompressed byte; at clean EOF raise on the **next** (terminal empty)
      `read` — `CorruptionError` for a digest mismatch, `TruncatedError` for a
      hash-less short. Do not withhold the last data chunk; do not raise from
      `finish_on_close`. The terminal empty `read` is the content-fault surface
      for chunked, not `close` (this replaces today's `_short`→`finish_on_close`
      deferral).
- [ ] 4.2 `readall` / `read(-1)` with digest mismatch or hash-less short: raise
      on that complete-stream call (`CorruptionError` / `TruncatedError`) so
      `read(); close()` cannot succeed quietly. Implement the sized branch as a
      **bounded drain loop** — read `min(chunk, remaining)` until `inner` returns
      `b""`, then run the EOF verdict. Do **not** rely on a single
      `inner.read(remaining)` (a `BinaryIO` inner may short-read, under-returning
      the body) and do **not** delegate to `inner.read(-1)` on the sized branch:
      the declared-size cap is a decompression-bomb bound (an over-long stream
      must stop at the declared size), so state that rationale in an inline
      comment. The unsized branch may keep `inner.read(-1)` then `_finish`. Also
      change `_finish` so the hash-less short **raises** `TruncatedError` on any
      read path (the terminal empty chunked `read` per 4.1, and this
      complete-stream call). `self._short`/`finish_on_close` MUST NOT stay a
      content-fault surface — `_finish` needs to distinguish a read-path call
      (raise the short) from a close-path call (never raise it).
- [ ] 4.3 `finish_on_close` closes the inner and MUST NOT introduce a first
      content `TruncatedError` / `CorruptionError` solely on close — for **either**
      a digest mismatch **or** a hash-less short (may still avoid a double-fault
      after `read` already failed, and may still surface a *teardown* error).
- [ ] 4.4 Update verify tests: keep
      `test_verify_mismatch_raises_at_eof_without_losing_final_chunk`; change
      close-raises cases to slurping-raises / chunked-then-empty-raises; add
      anti-footgun `read(); close()` must raise on bad CRC; keep “partial read
      then close is ok”; cover fused `ArchiveStream`+`MemberVerifier` path, not
      only the standalone `VerifyingStream` wrapper. Add a short-reading-inner
      case: `read(-1)` over an inner that returns `< n` without EOF must gather
      the full body and still fire the EOF verdict; and an over-long inner with a
      declared size must stop at the cap (`CorruptionError`), not slurp unbounded.

## 5. Docs + compose notes

- [ ] 5.1 Update `docs/internal/library-analysis.md` gzip row: stdlib path is
      gzip-window `DecompressorStream`, not `GzipFile`. Document (user-facing) the
      truncation behavior **and** its trade-off (resolved Decision, Open Question
      2): `data = f.read()` / `readall` on a truncated stream **raises and returns
      nothing** — a silent lossy success is worse than not salvaging — and the
      recoverable prefix is reachable only via a chunked `read(n)` loop.
- [ ] 5.2 Note in `docs/internal/open-issues.md` (or the rapidgzip change) that
      (a) empty→stdlib fallback SHOULD use this engine so a byte-at-a-time
      workaround is unnecessary, and (b) until that lands, truncated gzip
      behavior can still differ between accelerator ON and OFF — do not
      implement the rapidgzip fallback switch here unless that code is already
      present and trivial to retarget. (`_STDLIB_READ_SIZE = 1` is not in tree
      today.)

## 6. Verify

- [ ] 6.1 Targeted pytest for §§1–4 (`test_codecs`, accelerator-off gzip,
      zlib/deflate truncation, verify close/read contracts).
- [ ] 6.2 `uv run --no-sync ruff check` / `ruff format` on touched paths;
      `pyrefly check` + `ty check` clean.
- [ ] 6.3 `openspec validate --strict gzip-zlib-truncation-recovery`
