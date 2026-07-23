## Context

Provenance: PR #177 code review follow-up; PR #180 proposal review (feasibility
pass against current `DecompressorStream` / CPython `GzipFile` / `VerifyingStream`).

Mid-cut truncated gzip measurements (rapidgzip soft-empty → stdlib fallback path):

| Backend | Large `read(n)` / `read(-1)` | Recoverable prefix |
| --- | --- | --- |
| `gzip.GzipFile` | Raises `EOFError`; returns no bytes for that call | Only via tiny sized reads (`read(1)` …); oversize ask **discards** inflate output. Further `read(1)` on the **same** handle recovers nothing; reopen restores recovery. `close()` after truncate does **not** raise. |
| Raw `zlib.decompressobj(wbits=16+MAX_WBITS)` | Returns full prefix from `decompress`/`flush` | Full prefix at any max_length (~same length as GzipFile `read(1)` loop) |
| `ZlibDecompressorStream` today | Raises `TruncatedError` inside `_read_decompressed_chunk` after setting `_eof`, **before** `read()` returns already-filled `_buffer` | `read(1)` loop recovers the prefix then raises; `read(65536)` raises with the prefix stranded in `_buffer` (caller sees the error, not an empty return) |
| Deferred `pending_error` + return leftover | Return buffered/flush leftover; raise on next empty `read` | Full prefix even for `read(65536)` |

Also: bare `ZlibDecompressorStream(wbits=31)` stops after the **first** gzip member
(`unused_data` holds the rest). `GzipFile` / current `GzipCodec` concat all members.
Any gzip migration **must** chain members with GzipFile edge-case parity (below).

`compressed-streams` already defines `pending_error` for unix-compress leftover
bits. Incomplete zlib/gzip EOF should use the same mechanism instead of a hard
raise that drops bytes already buffered for the current `read`.

Rapidgzip empty→stdlib / ISIZE backstop remains owned by
`rapidgzip-truncation-investigation` (or its follow-ups). This change makes the
**stdlib** engine safe for large reads so that fallback (or `use_rapidgzip=OFF`)
does not need a hypothetical byte-at-a-time loop (`_STDLIB_READ_SIZE = 1` is not
in tree today — compose note only).

### Close vs read — what we guarantee today

| Surface | Truncation / corruption on `read`? | Same fault on `close`? |
| --- | --- | --- |
| `DecompressorStream` (zlib/xz/lzip/unix-compress, …) | Yes (`TruncatedError` / `CorruptionError`; unix-compress via `pending_error` on next empty `read`; `readall` raises today) | **No** — `close()` only closes the inner |
| `GzipCodec` via `gzip.GzipFile` | Yes (translated `EOFError` / `BadGzipFile` / `zlib.error`) | **No** after a truncating read |
| `_GzipTruncationCheckStream` (rapidgzip ISIZE) | Yes — check runs on empty `read` (EOF) | **No** dedicated close check |
| `VerifyingStream` / fused `MemberVerifier.finish_on_close` | Digest mismatch can raise from the terminal empty `read`; **but** a single `read()`/`read(-1)` that returns all available bytes without a follow-up empty read leaves short/digest verdicts to **`close()`** | **Yes** — `test_verify_expected_size_short_raises_truncated`, `test_verify_on_close_after_full_single_read`, etc. Code comment: defer short to close so a more specific inner teardown error can win |

So we do **not** yet guarantee “content errors never on `close`” for all stream
types. Decoder engines mostly already follow read-only signaling; the verifier
path is the deliberate exception. This change makes **never raise content
`TruncatedError`/`CorruptionError` on `close`** the standing rule and aligns the
verifier.

## Goals / Non-Goals

**Goals:**

1. Truncated DEFLATE-family streams through `DecompressorStream` deliver the
   recoverable prefix on bounded `read(n)`, then surface `TruncatedError` on the
   next empty `read` — never silent success via size/seek, never drop a prefix
   the decoder already has for that call.
2. Stdlib gzip (`GzipCodec` when rapidgzip is not selected) uses that engine with
   gzip-window inflate + GzipFile-parity multi-member chaining + CRC/ISIZE
   **outcomes** equivalent to `GzipFile`.
3. Large `read(n)` is safe (no byte-at-a-time requirement).
4. `tell` / `seek` on the stdlib gzip path stay correct (engine-owned `_pos`);
   truncated streams must not publish a clean complete size.
5. **`close()` never raises** decode/verify content faults (teardown `OSError` /
   translated inner-close failures may still propagate).

**Non-Goals:**

- Changing rapidgzip itself or the ISIZE / empty→stdlib backstop design (compose
  later by pointing fallback at this engine).
- DIY gzip random-access indexes (stdlib rewind remains O(n) with warning).
- Fixing bzip2 / other stdlib wrappers’ oversize-read traps in the same change
  (may note adjacency).
- Performance retune of `_COMPRESSED_READ_SIZE` beyond what correctness needs.

## Investigations

### GzipFile oversize-read trap

On truncated input, `GzipFile.read(n)` with `n` larger than remaining recoverable
output raises `EOFError` and returns nothing. Further `read(1)` on the **same**
handle recovers nothing. Reopening the file restores full recovery. `close()`
after that truncate is quiet. This is why a rapidgzip fallback that wraps
`gzip.open` would need `read(1)` loops.

### DecompressorStream hard-raise

```text
_read_decompressed_chunk:
  compressed EOF → leftover = flush()
  if not finished: raise TruncatedError  # _buffer already extended; raise aborts read()
```

A single `read(65536)` may have already `extend`ed `_buffer` with prior feed
output; the raise aborts before returning it. Fix: set `pending_error`, return
leftover (possibly empty); let `read` return buffered bytes; raise on next empty
`read`.

### Multi-member gzip (GzipFile parity — not a magic peek)

| API | Behavior |
| --- | --- |
| `gzip.GzipFile` | Concatenates members; skips **zero padding** between members; trailing zeros only → clean EOF; trailing non-gzip junk → `BadGzipFile` |
| `zlib.decompressobj(16+MAX_WBITS)` once | First member only; rest in `unused_data` (trailer already consumed by zlib) |
| `ZlibDecompressorStream` today | First member only |

CPython uses raw inflate (`wbits=-MAX_WBITS`) plus manual CRC/ISIZE in
`_read_eof`, then skips NULs before the next header. With `wbits=16+MAX_WBITS`,
zlib validates CRC/ISIZE itself (`incorrect data check` / `incorrect length
check` → `CorruptionError`); `unused_data` is post-trailer. Chaining must still
mirror `_read_eof`’s zero-skip and junk rules — a naive
`unused_data.startswith(b'\x1f\x8b')` **drops** a second member after NUL
padding and can mis-handle trailing garbage.

Need a `GzipDecoder` that, when a member completes, strips leading NULs from
`unused_data` / retained input, then: empty → finished; `1f 8b` → new
`decompressobj` and continue inside `feed` (same idea as lzip’s member loop);
anything else → `CorruptionError`.

### `read(-1)` vs chunked: one rule, two call shapes

Python cannot return bytes and raise from the same call. With **never raise on
`close`**, the VISION-safe split is:

| API | Truncation (`DecompressorStream`) | Digest/CRC (`VerifyingStream`) |
| --- | --- | --- |
| Bounded `read(n)` (`n >= 0`) | Return recoverable prefix; next empty `read` raises | Return every valid byte; after full body, next empty `read` raises `CorruptionError` |
| `readall` / `read(-1)` | **Raise** `TruncatedError` (complete-stream request includes EOF verdict) | **Raise** `CorruptionError` / short `TruncatedError` (same) |
| `close()` | Never content fault | Never content fault |

**Why `read(-1)` raises for both:** `data = stream.read()` is the default idiom.
If slurping returned the body and only armed a pending verdict for a follow-up
empty `read`, then `read(); close()` would **silently accept** bad CRC or a
truncated prefix — a foot-gun that undercuts VISION “damaged input is
first-class” and “don’t shoot yourself.” Chunked loops already express “give me
data until empty”; they keep deliver-then-next-empty-raises. Slurping asks for
the whole stream in one call and therefore must surface the EOF verdict in that
call.

**Rejected:** slurping returns body + raise only on a later empty `read` (CRC
silent-success on `read(); close()`). **Rejected:** return truncated prefix from
`readall` + raise on `close`.

### Size / `SEEK_END` hole (blocking if ignored)

Today, incomplete EOF sets `_eof` and raises **without** setting `_size`. After a
truncating `read(1)` loop, `seek(0, SEEK_END)` hits `assert self._size is not
None` (`AssertionError`) — already broken.

If `readall` were changed to return a prefix and still did
`self._size = self._pos`, `SEEK_END` / `try_get_size` would report a **clean
complete** size while truncation was only in `pending_error` — silent success,
forbidden by the recoverable-prefix / damaged-input goals.

**Required:** on incomplete EOF do not publish a successful complete `_size`;
`seek(SEEK_END)` and `try_get_size` must raise the pending truncation (or leave
size unknown and fail the seek path with `TruncatedError`), never assert and
never pretend the prefix is the full stream.

**Two writer sites, not one.** The size hole is not only in the
`_read_decompressed_chunk` EOF branch. `readall` (`decompressor_stream.py`) also
does `self._size = self._pos` unconditionally after its drain loop, *before* it
raises the deferred `pending_error`. Under the new contract that runs for a
truncated stream too, so a caller that catches the `TruncatedError` and then
calls `try_get_size` / `seek(SEEK_END)` reads the prefix length back as a clean
complete size. Both writer sites (the EOF branch and `readall`) MUST gate the
`_size` assignment on "not truncated" — i.e. `pending_error is None` **and** the
decoder is genuinely finished.

**Unix-compress already leaks this today (existing latent bug).** A truncated
`.Z` flush sets `finished=True` *and* `pending_error` (`unix_compress.py`), so
the current EOF branch skips the `not finished` raise and runs
`self._size = ...` + `self._index_built = True`. `try_get_size` / `seek(SEEK_END)`
on a truncated `.Z` therefore already report the prefix as a clean complete
stream — the very thing this change forbids. So the size-integrity fix is a
**behavior change for `.Z`**, not just gzip/zlib: the `.Z` tasks must add a
truncated-size assertion, not merely "confirm it stays green." The gate above
(`pending_error is None and finished`) is what makes the rule uniform, because
`finished` alone cannot distinguish clean-complete from truncated-but-finished.

### Stdlib / third-party stream compatibility

This contract is **stricter**, not BinaryIO-incompatible. Happy-path duck typing
matches stdlib file objects: `read(n)` returns bytes; clean EOF returns `b""`
without raising; `read(0)` is a no-op; `close()` does not raise content faults.

| Situation | Stdlib analogue | Our contract | Compatible? |
| --- | --- | --- | --- |
| Clean EOF | `b""` | `b""` | Yes |
| Truncated gzip, chunked `read` | `GzipFile` raises `EOFError` (often after a partial prefix; large `read(n)` may drop bytes) | Return recoverable prefix; next empty `read` raises `TruncatedError` | Yes — same “fail on read”, more bytes kept |
| Truncated / bad CRC, `read(-1)` | `GzipFile` raises (`EOFError` / `BadGzipFile`), typically returns no bytes | Raise `TruncatedError` / `CorruptionError` | Yes |
| Bad CRC, tiny chunked reads | `GzipFile` can deliver all bytes then raise `BadGzipFile` on the finishing read | Deliver all bytes; raise on terminal empty `read` | Yes — same shape |
| `shutil.copyfileobj` | Raises mid-copy on truncate/CRC | Raises after writing delivered bytes | Yes — no silent short success |
| `close()` after content fault | `GzipFile` quiet | Quiet for content faults | Yes |
| Exception type | `BadGzipFile` ⊂ `OSError`; `BadZipFile` ⊂ `Exception`; `EOFError` ⊄ `OSError` | `ArchiveyError` ⊂ `Exception` (not `OSError`) | **Stricter typing** — `except OSError` will not catch us (same as `BadZipFile`). Faults still propagate; they do not become silent `b""` |

**Migration / passing streams out:** consumers that loop `read(n)` until `b""`
and treat exceptions as failure keep working; they may see a typed
`ArchiveyError` where GzipFile raised `EOFError`/`BadGzipFile`. Consumers that
only catch `OSError` around reads will **not** swallow our errors (good: no
false empty). Do not wrap these streams in `io.BufferedReader` and expect
identical pending-error timing — buffer read-ahead can pull the terminal empty
`read` early (same class of issue as buffering any fault-at-EOF stream).

**Rejected as compatibility goals:** matching GzipFile’s “large `read(n)` discards
prefix” quirk; raising content faults from `close()`; subclassing `OSError` just
to match `BadGzipFile`.

### VerifyingStream / MemberVerifier — read-vs-close today

Chunked digest mismatch already matches the desired shape
(`test_verify_mismatch_raises_at_eof_without_losing_final_chunk`): every data
chunk is delivered, then the **terminal empty `read`** raises
`CorruptionError`. What is wrong today:

- A slurping `read()` / `read(-1)` that returns all available bytes never hits
  that empty follow-up, so digest/short verdicts fall through to
  `finish_on_close` (`test_verify_on_close_after_full_single_read`, short-size
  tests).
- `_finish` sets `_short = True` and defers hash-less shortfall to close.

**Required (ADR 0014):**

- **Size-unknown chunked:** deliver every data byte; raise `CorruptionError` on
  the terminal empty `read` — never on `close()`.
- **Size-declared:** the read that reaches the declared size is a verifying
  event; on digest mismatch / over-run raise `CorruptionError` and **withhold**
  that chunk. Truncation-shaped ends still deliver the available prefix as a
  short return; the next empty `read` raises `TruncatedError`.
- **`read(-1)` / `readall`:** run the EOF verdict as part of the complete-stream
  read and **raise** on mismatch/short (do not return success bytes then rely on
  a follow-up `read` or on `close`). Sized `read(-1)` drains in bounded steps
  capped by `expected_size` (decompression-bomb bound — never `inner.read(-1)`).
- Hash-less short uses the **same** timing (`TruncatedError`): the terminal empty
  chunked `read` raises, and `read(-1)` raises — **not** `close`.
  `finish_on_close` closes the inner only; it never introduces a first
  `TruncatedError` / `CorruptionError`. Teardown errors from `inner.close()` may
  still propagate.
- Partial read then close stays quiet (abandon before clean EOF).
- Anti-footgun: `data = stream.read(); stream.close()` with a bad CRC MUST raise
  on the `read()` (not succeed quietly).

## Decisions

1. **Use `pending_error` for incomplete zlib/gzip/deflate EOF** (not a hard raise
   that drops buffered bytes). Rationale: matches unix-compress; probe showed full
   recovery with large `read(n)`. **Rejected:** keep hard raise + document
   `read(1)` only; **Rejected:** reopen-on-EOFError hybrid for GzipFile fallback.

   **Where the error is set (decision — please confirm).** `pending_error` is a
   *decoder*-owned property (getter + `clear_pending_error`, no setter). The two
   codec families reach truncation differently: unix-compress self-detects it and
   sets `self._pending_error` inside its own `flush()` while reporting
   `finished=True`; zlib/deflate report `finished=False` (`decompressobj.eof`) and
   never set `pending_error`, so today the *stream* raises on `not finished`.
   Task 1.1 as originally worded ("set `pending_error` in `_read_decompressed_chunk`")
   would have the **stream** populate a **decoder** field the readers consult —
   inconsistent. **Resolved — decoder-owned.** Mirror unix-compress: each
   `Decoder.flush()` sets `self._pending_error = TruncatedError(...)` when it
   reaches compressed EOF `not finished`, so the decoder is the single owner for
   *all* codecs and the stream's EOF branch simply checks
   `self._decoder.pending_error` (dropping its own `raise`). The `_size` gate then
   keys off that one predicate (see the size-hole note above). Forward-only
   decoders whose `finished` is size-driven (BCJ, PPMd, Deflate64) also reach EOF
   `not finished` on truncation, so their `flush` must arm the error too, not just
   zlib/gzip — today the stream's `not finished` raise covers them uniformly, and
   dropping it moves that responsibility into each decoder. **Rejected:** add
   `set_pending_error` to the `Decoder` protocol and keep detection in the stream.

   **Scope — xz / lzip converted too (resolved, Open Question 3 → in-scope).**
   `XZStreamDecoder.flush` and `LzipDecoder.flush` today **raise `TruncatedError`
   directly** instead of arming `pending_error` + returning leftover, so on the
   shared engine they still drop already-buffered output on a large truncating
   `read(n)` — the same bug. This change converts them to the pending-error +
   return-leftover shape too, so the decoder-owns-detection rule is **uniform
   across every `DecompressorStream` codec**. The only remaining inconsistency is
   the rapidgzip accelerator path, which stays a separate follow-up.

   **Make the responsibility legible (self-explanatory code).** `flush()` today
   reads as "emit any final buffered output at EOF"; it does not advertise that it
   is the single point where a decoder detects truncation and arms
   `pending_error`. That contract MUST be documented on the `Decoder` protocol and
   `BaseDecoder` — `flush` is called exactly once at compressed EOF and is where
   incompleteness is recorded — so a future decoder author does not silently omit
   it. **Optional:** rename `flush` → `finish` / `finalize` to convey the
   end-of-stream-verdict role; weigh against the churn (it touches every decoder:
   `ZlibDecoder`, `BrotliDecoder`, `PpmdDecoder`, `BcjDecoder`, `Deflate64Decoder`,
   unix-compress, xz, lzip). Implementor's call; the documentation is required
   regardless.

2. **Never raise content `TruncatedError` / `CorruptionError` on `close()`** for
   decode and verify streams. Faults surface from `read` (bounded next-empty,
   or `readall`), and from size/seek paths that would otherwise report a clean
   complete stream. Teardown `OSError` / translated inner-close failures remain
   allowed. **Rejected:** raise pending truncation on `close` (surprises `with`
   after a successful body; diverges from GzipFile and from today’s
   `DecompressorStream`).

3. **`readall` / `read(-1)` raises on EOF content faults** — truncation →
   `TruncatedError`; digest mismatch → `CorruptionError`; hash-less short →
   `TruncatedError`. Complete-stream reads include the EOF verdict so
   `read(); close()` cannot silently accept bad/truncated content. Bounded
   `read(n)` remains the deliver-then-next-empty-raises API (recoverable
   truncate prefix; CRC after all chunked bytes). Keep unix-compress `readall`
   raise-immediately tests; align VerifyingStream slurping the same way.
   **Rejected:** slurping returns body + pending verdict only for a later empty
   `read` or for `close`.

4. **Incomplete EOF must not publish a clean `_size`**; `SEEK_END` /
   `try_get_size` raise pending truncation or leave size unknown — never
   `AssertionError`, never silent prefix-as-complete.

5. **Stdlib gzip → `GzipDecoder` on `DecompressorStream`** with
   `wbits=16 + MAX_WBITS`, multi-member chaining with **GzipFile parity** (NUL
   padding skip; trailing zeros = EOF; trailing junk = `CorruptionError`),
   CRC/ISIZE **outcomes** via zlib’s gzip window (equivalent to GzipFile’s
   manual check, not the same code path). Wire `GzipCodec.open` (non-accel
   path) to this instead of `gzip.GzipFile` / `gzip.open`. **Rejected:** wrap
   GzipFile forever with tiny reads; **Rejected:** raw `ZlibDecoder` without
   multi-member; **Rejected:** magic-only `startswith(b'\x1f\x8b')` without
   zero-skip / junk handling.

6. **Keep deflate (`wbits=-15`) and zlib-wrap (`MAX_WBITS`) on `ZlibDecoder`**;
   only gzip needs member chaining. Shared truncate-return fix in the stream
   engine benefits all three.

7. **Seek/tell:** engine already tracks `_pos`; no GzipFile dual-handle. When
   rapidgzip later falls back, it must **replace** the inner stream with this
   engine (invariant from PR #177 review) — noted as compose step, not implemented
   here unless that backstop already exists on the branch.

8. **`VerifyingStream` / `MemberVerifier` digest (CRC) contract (ADR 0014):**
   Content verdicts fire from a **`read()` that reaches the end**, never from
   `close()`. Size-declared vs size-unknown differ on *when* corruption is
   raised relative to the final data chunk:

   - **Size-declared** (`expected_size` set): the read that reaches the declared
     size is a verifying event (checksum **and** over-run). On digest mismatch or
     over-run it raises `CorruptionError` and **withholds** that chunk (returns no
     bytes for that call). Truncation-shaped ends still deliver the available
     prefix as a short return; the next empty `read` raises `TruncatedError`.
   - **Size-unknown**: deliver data bytes; raise `CorruptionError` on the
     EOS-observing (typically terminal empty) `read` — no mandatory one-chunk
     lookahead withhold. `read(-1)` / `readall` still raise in that call.
   - **`close()` / `finish_on_close`**: teardown only — never a first content
     `TruncatedError` / `CorruptionError`.
   - **Full-count `read(n)`** (`n ≥ 1`): every public path coalesces to `n` or a
     terminal boundary via `streamtools.read_full_count` (loop while full pieces
     arrive; **stop on the first short**). Stop-on-short preserves deferred
     `DecompressorStream` truncation (return the prefix now; raise on the next
     empty `read`). Do **not** reuse `read_exact`, which keeps pulling after a
     short and would collapse that deferral into the same call. Archivey inners
     are fill-or-EOF (`DecompressorStream`, typical `ZipExtFile`, `BytesIO`), so
     stop-on-short is full-count for healthy data; it is not a RawIO
     mid-stream-short coalesce.
   - **Seek:** forfeit checksum only; length / truncation / over-run stay on and
     key off bytes **actually read** (`_furthest_read_pos`). A seek that jumps
     to/past the declared size without reading the gap has it read back **and a byte
     probed past the declared size** at conclusion (`_verify_reaches_declared`), so
     `seek(declared_size)` neither silences truncation (short → `TruncatedError`) or
     over-run (long → `CorruptionError`) nor fabricates either on a complete member
     (`seek(size); read(1)` returns `b""`).

   **Rejected:** close-raises; **Rejected:** size-declared "deliver every byte then
   raise on empty" for digest mismatch (ADR revises the earlier Decision 8 text);
   **Rejected:** slurping returns success bytes while leaving CRC failure only for
   a later empty `read` (foot-gun with `read(); close()`).

   **`read(-1)` implementation — bounded drain, not a single read, and not
   `inner.read(-1)`.** Two current defects on the sized `read(-1)` path
   (`MemberVerifier.read` with `n<0`, `expected_size` set): (a) it issues a single
   `inner.read(remaining)`, but `inner` is an arbitrary `BinaryIO` that MAY short-
   read (return `< n` without EOF), so the slurp under-returns on any non-buffering
   inner — masked today only because `BytesIO`/`DecompressorStream` happen to read
   to EOF; and (b) on a full return it does `if data: return data` *before*
   `_finish`, deferring the verdict to a later empty read / `close`. The fix is a
   **bounded drain loop**: `read_full_count` of `min(chunk, remaining)` until
   `inner` returns `b""`, then run the EOF verdict in the same call (withhold on
   fault). It CANNOT delegate to `inner.read(-1)` on the sized branch: the
   declared `expected_size` is a hard **decompression-bomb cap**
   (`test_verify_expected_size_overlong_stops_at_declared_size`), and
   `inner.read(-1)` would pull a corrupt/adversarial over-long payload wholesale
   into RAM. The unsized branch (no cap) MAY keep `inner.read(-1)` then `_finish`.
   Opaque accelerator EOF exceptions may become `TruncatedError`, but `OSError` /
   `MemoryError` MUST propagate (CONTRIBUTING). Because this is a safety bound,
   the draining code must state the cap rationale inline.

9. **Oracle:** for golden truncation tests, compare decompressed prefix and error
   type against `gzip.GzipFile` with a `read(1)` loop (max recovery), not against
   a single large `GzipFile.read()`.

10. **Stdlib compatibility:** keep BinaryIO happy-path semantics; content faults
    raise from `read` as typed `ArchiveyError` (stricter than `BadGzipFile` ⊂
    `OSError`, aligned with `BadZipFile` ⊂ `Exception`). Do not adopt GzipFile’s
    oversize-read prefix drop for “compatibility.”

## Risks / Trade-offs

| Risk | Mitigation |
| --- | --- |
| Multi-member edge cases (NUL padding, false headers, trailing junk) | Explicit GzipFile/`_read_eof` rules in decoder + tests |
| Abandon after bounded `read(n)` without follow-up empty `read` | Documented; size/`SEEK_END` must not lie; prefer read-until-exception; slurping `read(-1)` still raises |
| `read(); close()` silently accepting bad CRC | Forbidden — slurping must raise; explicit anti-footgun test |
| VerifyingStream behavior change (tests that expect close-raises) | Update tests per ADR 0014: size-declared mismatch = withhold on reaching read; size-unknown = deliver then empty raises; slurping `read(-1)` raises; `close` quiet |
| CRC failure mid-member vs truncation | zlib gzip window → `zlib.error` → existing `CorruptionError` translation |
| Stdlib vs rapidgzip truncate behavior still differs | Documented known remaining inconsistency until rapidgzip follow-up |
| Perf of member chaining | Only on member boundary; hot path unchanged |
| Inner teardown errors on `close` | Still propagate; do not conflate with content truncation |

## Open Questions

**Resolved (maintainer, review round):**

1. **`pending_error` ownership (Decision 1).** Resolved decoder-owned: each
   `Decoder.flush` sets its own `pending_error` on `not finished` (mirroring
   unix-compress), the stream drops its `not finished` raise, and every
   size-driven forward-only decoder (BCJ / PPMd / Deflate64) arms it too. The
   `flush` truncation-detection responsibility MUST be documented on the `Decoder`
   protocol and `BaseDecoder`; renaming `flush` → `finish`/`finalize` is an
   optional implementor call, weighed against touching every decoder.

2. **Recoverable prefix is unreachable via `data = f.read()` (values trade-off).**
   Resolved: keep the anti-foot-gun. Salvage does **not** oblige `readall` /
   `read(-1)` to return partial data — a silent/lossy success is worse than not
   salvaging, so the complete-stream call raises and the prefix is reachable only
   via a chunked `read(n)` loop. The behavior **and** the trade-off MUST be
   documented user-facing (task 5.1): `data = f.read()` on a truncated stream
   raises and returns nothing; chunked reads recover the prefix.

**Resolved (review round 2):**

3. **xz / lzip scope → in-scope.** Convert `XZStreamDecoder.flush` /
   `LzipDecoder.flush` from raise-on-`flush` to the pending-error + return-leftover
   shape (mirroring zlib/gzip/unix-compress), with truncation tests, so the shared
   `DecompressorStream` truncate-return fix is uniform across **every** codec. The
   rapidgzip accelerator path stays a separate follow-up (the one acknowledged
   remaining inconsistency).

4. **Truncation vs corruption on a short-with-hash body → raise `TruncatedError`,
   documented best-effort.** When a body is short *and* carries a hash, the
   shortfall and the digest mismatch can be genuinely indistinguishable (a
   corruption could also shorten output). Raise **`TruncatedError`** (truncation is
   the more likely cause), and **document** it as a *best-effort* verdict — the two
   causes are not always separable, so the specific error type is a best guess, not
   a guarantee. Deferred (not in this change): reparenting `TruncatedError` under
   `CorruptionError`. That is a library-wide exception-hierarchy change touching
   every catcher, and `except ReadError` already catches both today, so it is left
   as its own future decision.

**Optional later (non-blocking):** audit stdlib `bz2` / other
`DecompressorStream` codecs for the same oversize-read / truncate-return gap
(consistency sweep).
