# 0014 — Integrity verdicts surface from reads, never from `close()`

- **Status:** proposed
- **Date:** 2026-07 (review of `gzip-zlib-truncation-recovery`, PR #183)
- **Provenance:** OpenSpec `compressed-streams` (digest verification, read-vs-close
  fault split); `VISION.md` (no silent success; damaged input is first-class);
  `verification-integrity-mode` change (STRICT opt-in)

## Context

When a member carries a stored checksum (CRC32 / digest) or, for encrypted members,
an authentication tag, archivey verifies it **as the member is read** — incrementally,
in the default (streaming) mode. Two facts make the *timing* of the verdict a real
design question rather than an implementation detail:

1. **A checksum/auth verdict is only known after the last content byte is consumed.**
   We hash bytes as they flow; pass/fail is decided at the end.
2. **A single `read()` call cannot both return bytes and raise.** So if the chunk that
   completes the member is the one that reveals the checksum is bad, we must choose
   between returning those bytes and raising.

A third fact governs how callers *reach* the end, and it forced an explicit contract
choice (see the **full-count `read`** decision below):

3. **In general, `read(n)` may return fewer than `n` bytes on non-terminal data** —
   the stream contract for `n ≥ 1` guarantees *at least one* byte; only `b""` means
   EOF. A short return, in the general contract, is *not* an end-of-stream signal.

This came to a head in review. An earlier iteration verified on `close()` when the
member had been fully consumed. Making `close()` teardown-only then reintroduced a
**silent-acceptance regression** for the most ordinary full-member read:

```python
with archive.open(member) as f:   # member.size == 500, stored CRC is wrong
    data = f.read(member.size)     # returns 500 bytes; nothing raises
process(data)                      # acts on corrupt data — no verdict, ever
```

A checksum library must never silently hand back content it knows (or could know) is
corrupt. So we must decide, precisely, **which call raises** — and, because fact (3)
means `read(member.size)` might not even reach the end, **what our own `read`
contract guarantees.**

### Options considered

- **A — raise on the read that reaches the end.** The read that completes the member
  and finds damage raises (returning no bytes for that call); `close()` never raises.
  Same *failure surface* as stdlib `zipfile` / `gzip` (fault on read, not close) — not
  byte-identical timing (their large-read / EOF shapes differ).
- **B — deliver every byte, then raise on the next read; `close()` as a backstop.**
  Uniform for corruption and truncation, but makes a *safety* guarantee depend on
  `close()` actually running — unreliable (GC-driven close is not guaranteed and
  swallows exceptions), and a content error from `close()` can mask or be masked by an
  exception already unwinding a `with` body.
- **C — verify only when the caller reads to `b""` / `read(-1)`; silent otherwise.**
  Simplest `close()` semantics, but silently skips verification on
  `read(member.size)` — the most natural whole-member read. Silent false confidence is
  the one outcome a checksum library must not produce.

### Two distinctions that resolve it

**Corruption vs. truncation.** These are not the same failure:

- **Truncation** yields bytes that are *correct but incomplete* — worth delivering
  (the recoverable-prefix salvage this change is built on).
- **Corruption** yields bytes that are *wrong* — no value in returning the final chunk
  of a member whose checksum just failed.

So "deliver every byte before failing" is right for truncation and pointless for
corruption. Option A is correct *for corruption*; truncation naturally serves its
prefix and fails on a later read.

**Full-count vs. up-to-`n` `read`.** The regression example only reaches the end — and
is therefore verifiable — if our `read(n)` returns the full count. Under an "up-to-`n`"
`read`, `f.read(500)` could hand back 400 on *healthy* data; a single-call caller stops
at 400, never reaches the declared size, and gets no verdict — the regression, intact.
The guarantee "read the declared size → verified" is real only if `read(n)` coalesces
up to `n` (like `io.BufferedReader`), returning short **only** at a terminal boundary.
We therefore commit to full-count `read`, which is also what `DecompressorStream`
already implements.

### Why leaving early stops unverified is sound

Under full-count `read`, a caller who wants the whole member reaches its end (they read
`member.size`, or read until `b""`) and gets the verdict there. A caller who stops
before the end deliberately opted out. There is no principled line between "stopped at
byte 50 of a 500-byte member" and "stopped at byte 110 of that same 500-byte member
when only 110 were decodable": both are early stops of a member whose declared size is
500. Verifying one but not the other only because it sits on the current EOF boundary
would be arbitrary. So **all** early stops are treated identically: no verdict, and
`close()` stays silent — while a member *read to its end* that is damaged always
raises, independent of whether `close()` runs.

## Decision

### Full-count `read`

archivey's stream `read(n)` (`n ≥ 1`) returns **exactly `n` bytes until a terminal
boundary** — clean end-of-stream, truncation, or a raised content error. It never
returns short on healthy, non-terminal data. Consequently a short return **means a
terminal boundary was reached**, and reading is a reliable way to reach a member's end
(`read(-1)`, read-until-`b""`, or `read(member.size)` for a size-declared member).
(`read(0)` is a no-op, never EOF. This full-count guarantee applies to the public
`ArchiveStream`/codec-stream `read`; it does not require arbitrary *inner* `BinaryIO`
objects to be full-count — the wrapper coalesces.)

### Where the verdict fires

Content-integrity verdicts (stored checksum / digest, encrypted-member authentication
tag, truncation, and declared-size over-run) are delivered from a **`read()` call,
never from `close()`**. `close()` is teardown-only. It MAY still propagate an
*unavoidable teardown signal raised by the underlying resource itself* — an `OSError`
closing a file descriptor, or a subprocess exit code that only arrives when the process
is reaped (e.g. `unrar` reporting a wrong password on close). It MUST NOT surface the
**verifier's own** content verdict (checksum / auth / truncation) as a first fault on
`close()`. One current construct violates this — the WinZip AES stream drains and
verifies its HMAC in its own `close()` — and is treated as a **known inconsistency to
remove** (`verification-integrity-mode` Decision 2), **not** a blessed carve-out; until
it is removed, this guarantee is library-wide *except* for that one path.

A member is verified at the moment a read **reaches its end** — whichever comes first:

- the read that consumes the member's **declared** size, or
- the read that reaches the **decoder's end-of-stream** (its in-band end marker, or the
  underlying source returning `b""`).

At that moment:

- **Corruption** (checksum / auth mismatch, or a mid-stream structural error): the
  reaching read raises `CorruptionError` and returns no bytes. The final chunk of a
  member known to be corrupt is **withheld** (for size-declared members; see the
  size-unknown consequence and open question below).
- **Over-run** (decode output exceeds the declared size — a corrupt length field): the
  read that would cross the declared size raises `CorruptionError`, **independent of
  the checksum** (the over-run itself is the corruption, even if the hash-so-far would
  pass). This is the same stop-at-declared-size → `CorruptionError` bound that
  `gzip-zlib-truncation-recovery` already specifies as the decompression-bomb cap.
- **Truncation / short** (decoder end-of-stream before the declared size, or an
  incomplete decode): every recoverable byte was delivered on the preceding full-count
  reads; the read that reaches the short end returns the remaining prefix (a **short
  return**, not an exception), and the *next* read raises `TruncatedError`.

Reaching the declared size is **always** a verifying event — checksum *and* over-run —
regardless of how the caller got there (`read(n)` with `n == remaining`, or a chunked
loop). The verdict is a property of *reaching the end*, not of the call shape.

A read that **stops before the end** — at any offset — produces **no verdict**, and
`close()` stays silent. A seek off the sequential frontier disables the **checksum**
verdict for the rest of the handle's life (incremental hashing assumes linear
consumption); structural truncation past the seek remains detectable in principle, but
for simplicity we disable both under one rule (see open question 3).

### Short returns are never corrupt bytes

Because corruption *raises* rather than returning short, a short return from a
full-count `read` is only ever **clean-but-complete** (you asked for more than the
member holds) or **truncated** (correct-but-incomplete prefix). It is never "here are
some wrong bytes." A single-call reader distinguishes the two by length:

- size-declared member: `len(data) == member.size` → complete and verified;
  `len(data) < member.size` → truncated (the shortfall is the tell), and reading again
  raises `TruncatedError`;
- size-unknown member: a bare `read(n)` cannot self-certify — read `-1` or to `b""`.

## Guarantee (for users)

> **Read a member to its end and a damaged member raises on a `read()`. Stop before
> the end and it is not verified. `close()` never raises a content error.**

"To its end" means `read(-1)` / `readall`, reading until `read()` returns `b""`, or —
for a member with a **declared** size — reading that many bytes. For that whole-member
read:

- an **exception** means the member is corrupt (`CorruptionError`) or truncated
  (`TruncatedError`) — **discard everything read; none of it is trustworthy**;
- a **full-length** return (`len == member.size`, or a subsequent `b""`) means the
  content was **checksum-verified**;
- a **short** return (`len < member.size`, no exception) means **truncation** — the
  bytes are correct but the member is incomplete; **"no exception" does not mean
  "complete."** Check the length, or read again to get the `TruncatedError`.

Corruption is caught whenever such a read reaches the end — independent of whether
`close()` is ever called. Callers who must verify **regardless** of access pattern
(partial reads, seeks, or "never release unverified bytes") use
`VerificationMode.STRICT` (`verification-integrity-mode`), which fully verifies a
member before returning any of it.

### Call × failure matrix (size-declared member)

| Call | Corrupt at full length | Truncated short of declared size |
| --- | --- | --- |
| `read(member.size)` | raises `CorruptionError` | returns short (`len < size`), **no exception** |
| `read(-1)` / `readall` | raises `CorruptionError` | raises `TruncatedError` |
| chunked until `b""` | raises on the read that reaches the size (withholds that chunk) | delivers the whole prefix (final read returns short); the read *past* the prefix raises `TruncatedError` |
| partial read, then `close()` | quiet (early stop) | quiet (early stop) |

The load-bearing asymmetry: **`read(member.size)` raises on corruption but returns a
short buffer on truncation** — because corruption yields wrong bytes (withheld) while
truncation yields a correct-but-incomplete prefix (delivered). A single-call caller who
wants to catch both must check `len(data) == member.size` (or use `read(-1)`); **"no
exception" does not mean "complete."** Size-unknown members have no `member.size` to
read to, so a bare `read(n)` cannot self-certify at all — use `read(-1)` /
read-to-`b""`.

## Open questions

1. **Encrypted members — a defaults question, not just docs.** Treating an
   authentication tag as just-another-checksum in streaming mode means the default
   *releases unauthenticated plaintext* before the tag is checked — a sharper footgun
   than emitting bytes with a bad CRC, and arguably in tension with VISION's "no silent
   success." Should **authenticated** members default to `STRICT` (or refuse to stream
   unless the caller explicitly opts into detection-not-prevention), rather than
   inheriting the CRC default? A deliberate defaults decision to make, not inherit; it
   does not change any mechanism above.
2. **Size-unknown "withhold the corrupt final chunk" requires lookahead.** For a
   size-declared member we know the boundary before releasing the final chunk, so we
   can withhold it. For a size-unknown member (e.g. standalone gzip) the CRC lives in a
   trailer *after* the last plaintext byte: the decoder emits the final plaintext and
   only sees end-of-stream on the next pull. Honoring "corruption caught on the last
   data read / final chunk withheld" for size-unknown members requires a **one-chunk
   delayed-release buffer** (never hand out chunk *N* until chunk *N+1*-or-EOS has been
   pulled). Do we (a) require that lookahead for uniform behavior (small constant
   buffering), or (b) accept that size-unknown members surface the verdict on the read
   that observes end-of-stream — possibly *after* the final data chunk was released —
   which still honors the top-level guarantee via `read(-1)` / read-to-`b""`?
   Recommendation: **(b)**, documented — the realistic size-unknown reader reads to
   `b""` anyway, and "detection, not prevention" already permits pre-verdict bytes.
3. **Seek and truncation.** A premature decoder end-of-stream before the declared size
   is a *structural* fact independent of the incremental hash, so `TruncatedError`
   could in principle still be raised after a seek even though `CorruptionError` cannot.
   We currently disable both under one "seek disables verification" rule for
   simplicity. Keep the simple rule, or preserve the still-sound truncation check across
   seeks? Recommendation: keep it simple, acknowledging we forgo a deliverable verdict.

## Consequences

- **Resolves the open question that parked `gzip-zlib-truncation-recovery` (#183).**
  This ADR exists *because* #183's review surfaced the read-vs-close / sized-read hole;
  the #183 implementation is on hold pending it. #183's earlier Decision 8 — a bounded
  `read(n)` on a digest mismatch delivers every byte and raises on the terminal empty
  read ("do not withhold the last data chunk";
  `test_verify_mismatch_raises_at_eof_without_losing_final_chunk`) — is **revised** by
  this ADR for the corruption case: the read that reaches the declared size (or decoder
  EOS) raises and **withholds** that chunk. When #183 resumes, its delta and that test
  are updated to the withhold-on-reaching-read rule so the two texts agree — not a live
  conflict, but the settlement that unblocks #183. **Truncation is unchanged** —
  #183's recoverable-prefix delivery stands.
- **`read(member.size)` on a corrupt size-declared member raises from that read** and
  returns nothing — closing the silent-acceptance regression, without depending on
  `close()`.
- **High-level helpers read to the true end.** `extract()` and any whole-member helper
  MUST consume each member with a *completing* read (`read(-1)`, or a sized read plus a
  drain to `b""`), so the default extraction path raises `TruncatedError` on a short
  member rather than silently writing a truncated file. The quiet-truncation short
  return is a **low-level** `read(member.size)` affordance for callers who opt into it,
  never the default `extract` behavior.
- **STRICT is proposed, not shipped (sequencing).** The escape hatch for "never release
  unverified / unauthenticated bytes" is `VerificationMode.STRICT`
  (`verification-integrity-mode`, still proposed). This ADR's STREAMING default is
  accepted now; STRICT is sequenced after. Until it lands, STREAMING —
  detection-not-prevention — is the only posture, so **encrypted members stream
  unauthenticated plaintext by default** in the interim.
- **Full-count `read` is now a contract**, not an accident. The wrapper must coalesce
  short inner reads (bounded reads must loop the inner to `n`-or-terminal, as the
  `read(-1)` drain already does). A short return from a public archivey stream is a
  documented terminal signal.
- **`close()` never raises a content fault.** Cleanup is safe; a content error can
  neither mask nor be masked by an exception unwinding a `with` body; safety does not
  hinge on `close()` running.
- **Detection, not prevention, in streaming mode.** Any bounded read returns some bytes
  before the final verdict; the guarantee is "you are told before you can conclude the
  member is complete-and-intact," not "you are told before you touch any bytes."
  Prevention is `VerificationMode.STRICT`.
- **Size-declared vs. size-unknown timing differs** (open question 2). Size-declared:
  verdict on the read that reaches the declared size (the decoder must also consume the
  trailing CRC/tag to validate before that read returns or raises). Size-unknown:
  verdict on the read that observes end-of-stream — the last data read only if a
  lookahead buffer is adopted, otherwise the following read.
- **Encrypted members are a sharper case** (open question 1). With a single tag over
  the whole member, streaming *must* release unverified plaintext; segment-authenticated
  (per-chunk AEAD) formats could verify each chunk before release, but archivey's
  current formats do not.
- **Exception type tells you what you can trust.** `CorruptionError` ⇒ the content is
  wrong; trust **nothing** already read from this member. `TruncatedError` ⇒ the bytes
  already returned are correct but the member is **incomplete**; a caller may keep a
  salvaged prefix if incompleteness is acceptable, but must not treat it as the whole
  member. Documented on the exceptions and in the streaming guide.

## Implementation notes

- **Trailer consumption on exact-size reads.** For a size-declared member,
  `read(member.size)` must pull the underlying CRC/auth trailer and validate *before*
  the call returns or raises — the read that reaches the declared size finalizes the
  hash.
- **Seek-state tracking.** Once a seek off the sequential frontier disables the
  checksum verdict, it stays disabled — a seek-forward-then-back-to-end must not
  re-enable a hash comparison over a non-contiguous byte range (false-positive
  `CorruptionError`).
- **Full-count coalescing.** Bounded `read(n)` on the verifier/wrapper must loop the
  inner stream until it has `n` bytes or a terminal boundary, so the full-count
  contract holds even over a short-reading inner `BinaryIO`.
