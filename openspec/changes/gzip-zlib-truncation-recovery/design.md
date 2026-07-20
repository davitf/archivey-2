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

### `readall` vs recoverable prefix vs never-raise-on-close

Python cannot return bytes and raise from the same call. With **never raise on
`close`**:

| API | Contract |
| --- | --- |
| Bounded `read(n)` (`n >= 0`) | Return recoverable prefix (including bytes already in `_buffer`); leave `pending_error`; **next empty `read` raises** |
| `readall` / `read(-1)` | **Raise** `TruncatedError` when incomplete EOF is discovered (keep today’s unix-compress `readall` behavior — do not return a prefix here) |
| `close()` | Never raises content `TruncatedError` / `CorruptionError` |
| Abandon after `read(n)` got the full prefix then `close()` without a follow-up empty `read` | Acknowledged gap (caller stopped before observing the fault); accepted trade-off vs close-raises. Mitigate by not lying on size/`SEEK_END` |

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

### VerifyingStream close-raises

`MemberVerifier._finish` sets `_short = True` on hash-less shortfall and defers
the raise to `finish_on_close`. Digest mismatch after a slurping `read()` that
never hits an empty follow-up also surfaces on `close`. That conflicts with the
standing close contract and with the main-spec digest wording (“terminal read” /
“bytes-returning full read raises and returns no bytes”). Align: raise short /
digest verdicts from the read path (`readall` / terminal empty `read`);
`finish_on_close` only closes the inner (and may still translate/probe only to
**abandon** duplicate faults after `read` already failed — never introduce a
first content error solely on close). Partial read then close remains quiet
(abandon before clean EOF).

## Decisions

1. **Use `pending_error` for incomplete zlib/gzip/deflate EOF** (not a hard raise
   that drops buffered bytes). Rationale: matches unix-compress; probe showed full
   recovery with large `read(n)`. **Rejected:** keep hard raise + document
   `read(1)` only; **Rejected:** reopen-on-EOFError hybrid for GzipFile fallback.

2. **Never raise content `TruncatedError` / `CorruptionError` on `close()`** for
   decode and verify streams. Faults surface from `read` (bounded next-empty,
   or `readall`), and from size/seek paths that would otherwise report a clean
   complete stream. Teardown `OSError` / translated inner-close failures remain
   allowed. **Rejected:** raise pending truncation on `close` (surprises `with`
   after a successful body; diverges from GzipFile and from today’s
   `DecompressorStream`).

3. **`readall` / `read(-1)` raises `TruncatedError`** when incomplete EOF is
   known — does **not** return the recoverable prefix on that call. Bounded
   `read(n)` remains the recoverable-prefix API. Keep unix-compress `readall`
   tests’ raise-immediately shape; extend the same rule to zlib/gzip/deflate.
   **Rejected:** return prefix from `readall` + raise on `close`.

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

8. **Align `VerifyingStream` / `MemberVerifier`** to the never-raise-on-close
   rule in this change (same capability). **Rejected:** leave verifier
   close-raises as permanent exceptions to the standing contract.

9. **Oracle:** for golden truncation tests, compare decompressed prefix and error
   type against `gzip.GzipFile` with a `read(1)` loop (max recovery), not against
   a single large `GzipFile.read()`.

## Risks / Trade-offs

| Risk | Mitigation |
| --- | --- |
| Multi-member edge cases (NUL padding, false headers, trailing junk) | Explicit GzipFile/`_read_eof` rules in decoder + tests |
| Abandon after `read(n)` without follow-up empty `read` misses truncation | Documented; size/`SEEK_END` must not lie; prefer read-until-exception |
| VerifyingStream behavior change (tests that expect close-raises) | Update tests to expect raise on `readall` / terminal empty `read` |
| CRC failure mid-member vs truncation | zlib gzip window → `zlib.error` → existing `CorruptionError` translation |
| Perf of member chaining | Only on member boundary; hot path unchanged |
| Inner teardown errors on `close` | Still propagate; do not conflate with content truncation |

## Open Questions

None blocking implement. Optional later: audit stdlib `bz2` for the same
oversize-read trap.
