## Context

Provenance: PR #177 code review follow-up. Mid-cut truncated gzip measurements
(rapidgzip soft-empty → stdlib fallback path):

| Backend | Large `read(n)` / `read(-1)` | Recoverable prefix |
| --- | --- | --- |
| `gzip.GzipFile` | Raises `EOFError`; returns no bytes for that call | Only via tiny sized reads (`read(1)` …); oversize ask **discards** inflate output |
| Raw `zlib.decompressobj(wbits=16+MAX_WBITS)` | Returns full prefix from `decompress`/`flush` | Full (~7528 on the mid-cut fixture) at any max_length |
| `ZlibDecompressorStream` today | Raises `TruncatedError` in `_read_decompressed_chunk` after `flush` **without returning** leftover; already-filled `_buffer` is stranded | `read(1)` loop recovers most; `read(65536)` appears empty while `_buffer` holds the full prefix |
| Deferred `pending_error` probe | Return leftover; raise on next empty read | Full prefix even for `read(65536)` |

Also: bare `ZlibDecompressorStream(wbits=31)` stops after the **first** gzip member
(`unused_data` holds the rest). `GzipFile` / current `GzipCodec` concat all members.
Any gzip migration **must** chain members.

`compressed-streams` already defines `pending_error` for unix-compress leftover
bits. Incomplete zlib/gzip EOF should use the same mechanism instead of a hard
raise that drops bytes. Today `readall` intentionally drops prefix when
`pending_error` is set — that fights recoverable-prefix priority and must change.

Rapidgzip empty→stdlib / ISIZE backstop remains owned by
`rapidgzip-truncation-investigation` (or its follow-ups). This change makes the
**stdlib** engine safe for large reads so that fallback (or `use_rapidgzip=OFF`)
does not need `_STDLIB_READ_SIZE = 1`.

## Goals / Non-Goals

**Goals:**

1. Truncated DEFLATE-family streams through `DecompressorStream` deliver the
   recoverable prefix, then surface `TruncatedError` (next empty read and/or
   `close`) — never silent success, never drop a prefix the decoder already has.
2. Stdlib gzip (`GzipCodec` when rapidgzip is not selected) uses that engine with
   gzip-window inflate + multi-member chaining + CRC behavior equivalent to
   `GzipFile`.
3. Large `read(n)` / `read(-1)` are safe (no byte-at-a-time requirement).
4. `tell` / `seek` on the stdlib gzip path stay correct (engine-owned `_pos`).

**Non-Goals:**

- Changing rapidgzip itself or the ISIZE / empty→stdlib backstop design (compose
  later by pointing fallback at this engine).
- DIY gzip random-access indexes (stdlib rewind remains O(n) with warning).
- Fixing bzip2 / other stdlib wrappers in the same change (may note adjacency).
- Performance retune of `_COMPRESSED_READ_SIZE` beyond what correctness needs.

## Investigations

### GzipFile oversize-read trap

On truncated input, `GzipFile.read(n)` with `n` larger than remaining recoverable
output raises `EOFError` and returns nothing. Further `read(1)` on the **same**
handle recovers nothing. Reopening the file restores full recovery. This is why
a rapidgzip fallback that wraps `gzip.open` needed `read(1)` loops.

### DecompressorStream hard-raise

```text
_read_decompressed_chunk:
  compressed EOF → leftover = flush()
  if not finished: raise TruncatedError  # leftover not returned
```

A single `read(65536)` may have already `extend`ed `_buffer` with prior feed
output; the raise aborts before returning it. Fix: set `pending_error`, return
leftover (and let `read` return buffered bytes); raise on next empty `read`.

### Multi-member gzip

| API | Multi-member |
| --- | --- |
| `gzip.GzipFile` | Concatenates all members |
| `zlib.decompressobj(16+MAX_WBITS)` once | First member only; rest in `unused_data` |
| `ZlibDecompressorStream` today | First member only |

Need a `GzipDecoder` (or zlib decoder mode) that, when `eof` and `unused_data`
looks like another gzip member, starts a new `decompressobj` — same idea as
lzip’s member loop.

### `readall` vs unix-compress tests

`test_unix_compress_truncated_readall_raises` expects `read()` to raise and not
return a prefix. Recoverable-prefix priority requires: return prefix from
`readall`, keep `pending_error`, raise on subsequent empty `read` or `close`.
Update that test; chunked unix-compress behavior stays the model.

## Decisions

1. **Use `pending_error` for incomplete zlib/gzip/deflate EOF** (not a hard raise
   that drops bytes). Rationale: matches unix-compress; probe showed full
   recovery with large reads. **Rejected:** keep hard raise + document
   `read(1)` only; **Rejected:** reopen-on-EOFError hybrid for GzipFile fallback.

2. **`readall` / `read(-1)` returns the recoverable prefix** and leaves
   `TruncatedError` in `pending_error`. The next empty `read` **or** `close`
   raises it (extend `DecompressorStream.close` to raise pending after closing
   the inner, mirroring the rapidgzip fallback close pattern). **Rejected:**
   keep “drop prefix on readall” — fights design priority (2). Update
   unix-compress `readall` test to assert prefix via chunked reads or
   return-then-close-raises.

3. **Stdlib gzip → `GzipDecoder` on `DecompressorStream`** with
   `wbits=16 + MAX_WBITS`, multi-member chaining via `unused_data`, trailer CRC
   enforced by zlib’s gzip window (same as today). Wire `GzipCodec.open` (non-accel
   path) to this instead of `gzip.GzipFile` / `gzip.open`. **Rejected:** wrap
   GzipFile forever with tiny reads; **Rejected:** use raw `ZlibDecoder` without
   multi-member (breaks concatenated gzip).

4. **Keep deflate (`wbits=-15`) and zlib-wrap (`MAX_WBITS`) on `ZlibDecoder`**;
   only gzip needs member chaining. Shared truncate-return fix in the stream
   engine benefits all three.

5. **Seek/tell:** engine already tracks `_pos`; no GzipFile dual-handle. When
   rapidgzip later falls back, it must **replace** the inner stream with this
   engine (invariant from PR #177 review) — noted as compose step, not implemented
   here unless that backstop already exists on the branch.

6. **Oracle:** for golden tests, compare decompressed prefix and error type
   against `gzip.GzipFile` with a `read(1)` loop (max recovery), not against a
   single large `GzipFile.read()`.

## Risks / Trade-offs

| Risk | Mitigation |
| --- | --- |
| Multi-member edge cases (padding, false headers) | Mirror GzipFile / RFC 1952 member loop; corpus multi-member tests |
| `close` raising surprises callers | Same pattern already used elsewhere; document; tests with `with` |
| unix-compress `readall` behavior change | Explicit test update; chunked contract unchanged |
| CRC failure mid-member vs truncation | zlib gzip window raises `zlib.error` → existing `CorruptionError` translation |
| Perf of member chaining | Only on member boundary; hot path unchanged |

## Open Questions

None blocking implement. Optional later: apply the same truncate-return fix
audit to other stdlib wrappers (`bz2`) if they share the oversize-read trap.
