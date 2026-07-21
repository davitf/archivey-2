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

Chunked digest mismatch already matches the desired shape
(`test_verify_mismatch_raises_at_eof_without_losing_final_chunk`): every data
chunk is delivered, then the **terminal empty `read`** raises
`CorruptionError`. What is wrong today:

- A slurping `read()` / `read(-1)` that returns all available bytes never hits
  that empty follow-up, so digest/short verdicts fall through to
  `finish_on_close` (`test_verify_on_close_after_full_single_read`, short-size
  tests).
- `_finish` sets `_short = True` and defers hash-less shortfall to close.

**Required:**

- **Chunked:** provide **all** decompressed bytes on data-returning reads; check
  CRC/digest at clean EOF; raise `CorruptionError` on the **next** empty
  `read` — never by dropping the last data chunk, never on `close()`.
- **`read(-1)` / `readall`:** run the EOF verdict as part of the complete-stream
  read and **raise** on mismatch/short (do not return success bytes then rely on
  a follow-up `read` or on `close`). Implementation may drain via bounded reads
  so the terminal empty `read` naturally fires inside `readall`.
- Hash-less short uses the same timing (`TruncatedError`).
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
   inconsistent. **Chosen (recommend):** mirror unix-compress — give
   `ZlibDecoder`/`GzipDecoder` a `flush()` that sets
   `self._pending_error = TruncatedError(...)` when `not self._decomp.eof`, so the
   decoder is the single owner for *all* codecs and the stream's EOF branch simply
   checks `self._decoder.pending_error` (dropping its own `raise`). The `_size`
   gate then keys off that one predicate (see the size-hole note above).
   **Alternative (if preferred):** add an explicit `set_pending_error` to the
   `Decoder` protocol and keep detection in the stream. Confirm which before
   implementing 1.1 / 2.x — it changes the decoder API surface either way.
   Note: forward-only decoders whose `finished` is size-driven (BCJ, PPMd,
   Deflate64) also reach EOF `not finished` on truncation; whichever mechanism is
   chosen must cover them, not just zlib/gzip (today the stream's `not finished`
   raise covers them uniformly — dropping it means each such `flush` must set the
   error, or the stream must retain a fallback).

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

8. **`VerifyingStream` / `MemberVerifier` digest (CRC) contract:** on bounded
   reads, deliver every byte then raise `CorruptionError` on the terminal empty
   `read`. On `read(-1)` / `readall`, raise `CorruptionError` as part of that
   complete-stream call (same for hash-less short → `TruncatedError`). Never
   raise digest/short on `close()`. **Rejected:** close-raises; **Rejected:**
   slurping returns success bytes while leaving CRC failure only for a later
   empty `read` (foot-gun with `read(); close()`).

   **`read(-1)` implementation — bounded drain, not a single read, and not
   `inner.read(-1)`.** Two current defects on the sized `read(-1)` path
   (`MemberVerifier.read` with `n<0`, `expected_size` set): (a) it issues a single
   `inner.read(remaining)`, but `inner` is an arbitrary `BinaryIO` that MAY short-
   read (return `< n` without EOF), so the slurp under-returns on any non-buffering
   inner — masked today only because `BytesIO`/`DecompressorStream` happen to read
   to EOF; and (b) on a full return it does `if data: return data` *before*
   `_finish`, deferring the verdict to a later empty read / `close`. The fix is a
   **bounded drain loop**: read `min(chunk, remaining)` until `inner` returns
   `b""`, then run the EOF verdict in the same call. It CANNOT delegate to
   `inner.read(-1)` on the sized branch: the declared `expected_size` is a hard
   **decompression-bomb cap** (`test_verify_expected_size_overlong_stops_at_declared_size`
   asserts `inner.tell() <= declared + 1`), and `inner.read(-1)` would pull a
   corrupt/adversarial over-long payload wholesale into RAM. The unsized branch
   (no cap) MAY keep `inner.read(-1)` then `_finish`. Because this is a safety
   bound, the draining code must state the cap rationale inline.
   Note the plain `if n < 0: _finish()` shortcut is sufficient *only* for the
   unsized branch; the sized branch needs the loop even so.

   **`_finish` must raise the hash-less short on this path, not defer it.** Today
   `_finish` sets `self._short = True` for a hash-less shortfall and leaves the
   raise to `finish_on_close`. On the complete-stream `read(-1)` path it must
   instead raise `TruncatedError` directly; `self._short`/close stays the
   mechanism only for the chunked terminal-empty-read shape.

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
| VerifyingStream behavior change (tests that expect close-raises) | Update tests: chunked = all bytes then empty `read` raises; slurping `read(-1)` raises; `close` quiet |
| CRC failure mid-member vs truncation | zlib gzip window → `zlib.error` → existing `CorruptionError` translation |
| Stdlib vs rapidgzip truncate behavior still differs | Documented known remaining inconsistency until rapidgzip follow-up |
| Perf of member chaining | Only on member boundary; hot path unchanged |
| Inner teardown errors on `close` | Still propagate; do not conflate with content truncation |

## Open Questions

**Decide before implementing:**

1. **`pending_error` ownership (Decision 1).** Confirm the recommended
   decoder-owned mechanism (each `Decoder.flush` sets its own `pending_error` on
   `not finished`, mirroring unix-compress; the stream drops its `not finished`
   raise) vs. adding a `set_pending_error` to the protocol. Affects tasks 1.1 and
   2.x and the `Decoder` API. The chosen mechanism must also cover the size-driven
   forward-only decoders (BCJ / PPMd / Deflate64), not only zlib/gzip.

2. **Recoverable prefix is unreachable via `data = f.read()` (values trade-off).**
   Decision 3 makes `readall` / `read(-1)` **raise** on truncation and drop the
   prefix; only a chunked `read(n)` loop can salvage it. This trades VISION §1.4
   "damaged input is first-class / recoverable members" against the
   `read(); close()` anti-foot-gun — a deliberate, defensible call, but it pits two
   VISION values against each other, so it wants explicit maintainer sign-off
   rather than living only here. If accepted, the user-facing docs (task 5.1) must
   state that prefix salvage requires chunked reads. Confirm.

**Optional later (non-blocking):** audit stdlib `bz2` / other
`DecompressorStream` codecs for the same oversize-read / truncate-return gap
(consistency sweep). Minor honesty nuance: for a truncated stream that *also*
carries a hash, `_finish` raises `CorruptionError` (partial-data digest mismatch)
rather than `TruncatedError`; not wrong, but `TruncatedError` would be the more
honest verdict — worth a glance, not a blocker.
