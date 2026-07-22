# Tasks — gzip via zlib DecompressorStream + recoverable truncation

## 1. DecompressorStream truncate-return + size integrity

- [x] 1.1 Change `_read_decompressed_chunk` incomplete-EOF path: return flush
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
- [x] 1.2 Bounded `read(n)` returns the recoverable prefix while leaving
      `pending_error` set; raise on the next empty `read`; clear via
      `clear_pending_error` after raise / seek reset.
- [x] 1.3 Keep `readall` / `read(-1)` raising when `pending_error` is set after
      drain (no prefix returned from that call) — same shape as today’s
      unix-compress `readall` test.
- [x] 1.4 `close()` MUST NOT raise `pending_error` (teardown-only for content
      faults).
- [x] 1.5 Fix `seek(SEEK_END)` / `try_get_size` after incomplete EOF: raise
      pending truncation or leave size unknown — never `AssertionError`, never
      silent prefix-as-complete.
- [x] 1.6 Confirm unix-compress chunked “prefix then next empty read raises”
      stays green; `readall` still raises; `close` after observed truncation is
      quiet.
- [x] 1.7 **Behavior change (not "stays green"):** a truncated `.Z` currently
      publishes a clean complete `_size` (flush reports `finished=True` +
      `pending_error`), so `try_get_size` / `seek(SEEK_END)` report the prefix as
      complete. Assert (red→green) that after the size-gate fix a truncated `.Z`
      reports size unknown or raises `TruncatedError`, never the prefix length.
- [x] 1.8 **xz / lzip flush → pending_error (uniform engine, Open Question 3).**
      `XZStreamDecoder.flush` (`xz.py`) and `LzipDecoder.flush` (`lzip.py`)
      currently raise `TruncatedError` on incomplete EOF, dropping buffered
      output. Convert both to arm `pending_error` + return any flush/leftover
      bytes (mirroring the zlib/gzip/unix-compress decoders), and gate `_size`
      the same way (1.1). The rapidgzip accelerator path is explicitly **not**
      converted here (separate follow-up).

## 2. GzipDecoder + GzipCodec stdlib path

- [x] 2.1 Add a gzip-window decoder (`wbits=16+MAX_WBITS`) that chains members
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
- [x] 2.2 Wire `GzipCodec.open` (non-accelerator path) to
      `DecompressorStream` + that decoder; remove `gzip.open` / `GzipFile` as
      the decode engine. Keep exception translation equivalent (`zlib.error` →
      `CorruptionError`; engine `TruncatedError` passes through).
- [x] 2.3 Confirm CRC/ISIZE failures still map to `CorruptionError` (zlib gzip
      window outcomes); intact single- and multi-member files match
      `gzip.GzipFile` oracle output.
- [x] 2.4 Ensure `tell` / `seek` (when seekable) track engine `_pos` correctly on
      the new path; rewind warning unchanged when accelerator off.

## 3. Truncation recovery + multi-member tests

- [x] 3.1 Truncated single-member gzip: assert large `read(n)` recovers the same
      correct prefix as a `read(1)` loop, then `TruncatedError` on next empty
      `read` (oracle = GzipFile `read(1)` loop max). `readall` raises.
      `close()` after the error was observed on `read` succeeds.
- [x] 3.2 Same engine-level coverage for truncated raw deflate / zlib through
      `ZlibDecompressorStream` (proves 1.x is not gzip-only).
- [x] 3.3 Multi-member intact gzip fully concatenates; zero-padded multi-member
      concatenates; trailing zeros only → clean EOF; trailing junk →
      `CorruptionError`; truncated mid-second-member delivers prefix through
      first member + partial second then truncates loudly on the read path.
      Cover the cross-`feed` edges (feed the compressed bytes in small chunks so
      boundaries land mid-run): NUL padding split across a feed boundary still
      concatenates; a member boundary read one byte at a time loses no bytes; a
      lone trailing `1f` at EOF raises `CorruptionError` (not a dropped member).
- [x] 3.4 Valid empty gzip and valid empty-payload member still succeed with
      zero bytes.
- [x] 3.5 After truncated decode, `seek(SEEK_END)` / size APIs do not report a
      clean complete prefix size (raise or unknown per 1.5).
- [x] 3.6 Update existing gzip truncate tests that assume `read()`/`readall`
      shapes under the new stdlib backend (e.g. `test_truncated_gzip_translates_to_truncated`).
- [x] 3.7 xz and lzip (1.8): truncated stream — large `read(n)` recovers the
      prefix then `TruncatedError` on the next empty `read`; `readall` raises;
      `close` after the observed error is quiet; `seek(SEEK_END)` / size does not
      report a clean complete prefix size. Update any existing xz/lzip truncate
      tests that assumed raise-from-`flush` shapes.

## 4. VerifyingStream / MemberVerifier — ADR 0014 read-path verdicts

- [x] 4.1 Bounded `read(n)` digest/CRC mismatch:
      - **Size-declared:** raise `CorruptionError` on the reaching read and
        **withhold** that chunk (ADR 0014 revises the earlier "deliver every
        byte" Decision 8 text).
      - **Size-unknown:** return every decompressed byte; raise on the terminal
        empty `read`.
      Hash-less short: deliver available prefix; raise `TruncatedError` on the
      next empty `read`. Do not raise from `finish_on_close`.
- [x] 4.1b **Full-count `read(n)`** on `MemberVerifier` and `ArchiveStream`
      passthrough (via `streamtools.read_full_count` (stop on short)) so `read(member.size)` reaches
      the declared size over short-reading inners.
- [x] 4.1c **Seek:** forfeit checksum only; length / truncation / over-run
      checks remain active after a seek off the sequential frontier.
- [x] 4.2 `readall` / `read(-1)` with digest mismatch or hash-less short: raise
      on that complete-stream call (`CorruptionError` / `TruncatedError`) so
      `read(); close()` cannot succeed quietly. Implement the sized branch as a
      **bounded drain loop** — read `min(chunk, remaining)` until `inner` returns
      `b""`, then run the EOF verdict (withhold on fault). Do **not** rely on a
      single `inner.read(remaining)` and do **not** delegate to `inner.read(-1)`
      on the sized branch (decompression-bomb cap). The unsized branch may keep
      `inner.read(-1)` then `_finish`.
- [x] 4.3 `finish_on_close` closes the inner and MUST NOT introduce a first
      content `TruncatedError` / `CorruptionError` solely on close — for **either**
      a digest mismatch **or** a hash-less short (may still avoid a double-fault
      after `read` already failed, and may still surface a *teardown* error).
- [x] 4.4 Update verify tests: size-unknown keep deliver-then-empty; add
      size-declared withhold on reaching read; full-count over short-reading
      inners; seek forfeits checksum but keeps length; slurping-raises /
      anti-footgun `read(); close()`; fused `ArchiveStream`+`MemberVerifier`
      path; over-long inner stops at the cap.

## 5. Docs + compose notes

- [x] 5.1 Update `docs/internal/library-analysis.md` gzip row: stdlib path is
      gzip-window `DecompressorStream`, not `GzipFile`. Document (user-facing) the
      truncation behavior **and** its trade-off (resolved Decision, Open Question
      2): `data = f.read()` / `readall` on a truncated stream **raises and returns
      nothing** — a silent lossy success is worse than not salvaging — and the
      recoverable prefix is reachable only via a chunked `read(n)` loop.
- [x] 5.1b Document the truncation-vs-corruption verdict (Open Question 4): a
      short body that also carries a hash raises `TruncatedError`, but the two
      causes can be indistinguishable, so the specific error type is **best-effort**
      (a corrupt stream may surface as `TruncatedError` and vice versa). Reparenting
      `TruncatedError` under `CorruptionError` is out of scope; `except ReadError`
      already catches both.
- [x] 5.2 Note in `docs/internal/open-issues.md` (or the rapidgzip change) that
      (a) empty→stdlib fallback SHOULD use this engine so a byte-at-a-time
      workaround is unnecessary, and (b) until that lands, truncated gzip
      behavior can still differ between accelerator ON and OFF — do not
      implement the rapidgzip fallback switch here unless that code is already
      present and trivial to retarget. (`_STDLIB_READ_SIZE = 1` is not in tree
      today.)

## 6. Verify

- [x] 6.1 Targeted pytest for §§1–4 (`test_codecs`, accelerator-off gzip,
      zlib/deflate/xz/lzip truncation, verify close/read contracts).
- [x] 6.2 `uv run --no-sync ruff check` / `ruff format` on touched paths;
      `pyrefly check` + `ty check` clean.
- [x] 6.3 `openspec validate --strict gzip-zlib-truncation-recovery`
