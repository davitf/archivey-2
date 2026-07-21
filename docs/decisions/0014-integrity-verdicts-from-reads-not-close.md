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

A subtle third fact governs how callers *reach* the end, and it corrects a tempting
but wrong heuristic:

3. **A short read is not an end-of-stream signal.** The stream `read(n)` contract
   (for `n ≥ 1`) guarantees *at least one* byte and permits *fewer than `n`*; only a
   return of `b""` means EOF. So `read(500)` returning `110` does **not** tell the
   caller the stream ended — a caller who wants the whole member is expected to keep
   reading. What actually reveals truncation is: **keep reading toward the size you
   expect, and you eventually get an error (or `b""`) from a `read()`.**

This came to a head in review. An earlier iteration verified on `close()` when the
member had been fully consumed. That was then made teardown-only ("`close()` never
raises a content fault"), which reintroduced a **silent-acceptance regression**: a
routine full-member read via a sized call —

```python
with archive.open(member) as f:   # member.size == 500, stored CRC is wrong
    data = f.read(member.size)     # returns 500 bytes; nothing raises
process(data)                      # acts on corrupt data
```

— produced no error at all. A checksum library must never silently hand back content
it knows (or could know) is corrupt. So we must decide, precisely, **which call
raises**.

### Options considered

- **A — raise on the read that reaches the end.** The read that completes the member
  and finds damage raises (returning no bytes for that call); `close()` never raises.
  Matches stdlib `zipfile` / `gzip`. Downside as first stated: naively applied to
  *truncation*, it would drop the recoverable prefix (which this whole change exists
  to deliver).
- **B — deliver every byte, then raise on the next read; `close()` as a backstop.**
  Every read returns its bytes; the trailing (empty) read raises, or `close()` does if
  the caller stops exactly at the end. Uniform for corruption and truncation, but
  makes a *safety* guarantee depend on `close()` actually running — unreliable
  (GC-driven close is not guaranteed and swallows exceptions), and a content error
  from `close()` can mask or be masked by an exception already unwinding a `with`
  body.
- **C — verify only when the caller reads to `b""` (or `read(-1)`); silent otherwise.**
  Simplest `close()` semantics, but silently skips verification on
  `read(member.size)` — the most natural way to consume a whole member. Silent false
  confidence is the one outcome a checksum library must not produce.

### The distinction that resolves A-vs-B

Corruption and truncation are not the same failure:

- **Truncation** yields bytes that are *correct but incomplete* — worth delivering
  (the recoverable-prefix salvage this change is built on). The failing read is the
  one that reaches the end while still short.
- **Corruption** yields bytes that are *wrong* — there is no value in handing back the
  final chunk of a member whose checksum just failed.

So "deliver every byte before failing" is right for truncation and pointless for
corruption. Option A is correct *for corruption*; truncation naturally serves its
prefix and fails on the read that reaches the end.

### Why leaving early stops unverified is sound

Given fact (3), a caller who genuinely wants the whole member keeps reading until they
have the expected size (or `b""`) — and therefore *reaches the end on some read* and
gets the verdict there. A caller who stops before the end has deliberately opted out.
There is no principled line between "stopped at byte 50 of 110 available" and "stopped
at byte 110 of 110 available": both are early stops, and a short read did not tell the
second caller anything the first didn't know. Verifying one but not the other only
because it sits on the EOF boundary would be arbitrary. So **all** early stops are
treated identically: no verdict.

This keeps the safety property — *a member read to its end that is damaged always
raises* — while making it independent of `close()`.

## Decision

Content-integrity verdicts (stored checksum / digest, encrypted-member authentication
tag, and short/truncation) are delivered from a **`read()` call, never from
`close()`**. `close()` is teardown-only; it may still propagate a resource/teardown
error (a subprocess exit code, an inner stream that authenticates in its *own*
`close()`), but it never introduces a first content fault.

A member is verified at the moment a read **reaches its end** — whichever comes first:

- the read that consumes the member's **declared** size, or
- the read that reaches the **decoder's end-of-stream** (its in-band end marker, or the
  underlying source returning `b""`).

At that moment:

- **Corruption** (checksum / auth mismatch): the reaching read raises `CorruptionError`
  and returns no bytes. The final chunk of a member known to be corrupt is **withheld**.
- **Truncation / short** (decoder EOF before the declared size, or an incomplete
  decode): every recoverable byte was delivered on the preceding bounded reads; the
  read that reaches the end while still short raises `TruncatedError`.

A read that **stops before the end** — at any offset, including exactly the bytes
currently available — produces **no verdict**, and `close()` stays silent. A seek off
the sequential frontier disables verification for the rest of the handle's life
(incremental hashing assumes linear consumption).

**Guarantee, stated for users:** *Read a member to its end and a damaged member raises
on a `read()`. Stop before the end and it is not verified. `close()` never raises a
content error.* "To its end" means `read(-1)` / `readall`, reading until `read()`
returns `b""`, or reading the declared size. Corruption is caught whenever such a read
reaches the end — independent of whether `close()` is ever called.

Callers who must verify **regardless** of access pattern — partial reads, seeks, or
"never release unverified bytes" — use `VerificationMode.STRICT`
(`verification-integrity-mode`), which fully verifies a member before returning any of
it. This is the required posture for untrusted input where release of unauthenticated
plaintext is unacceptable (see Consequences).

## Consequences

- **`read(member.size)` on a corrupt member raises from that read** and returns
  nothing — closing the silent-acceptance regression, and doing so without depending
  on `close()`.
- **This reverses "deliver every byte even on a checksum mismatch."** A corrupt
  member's final chunk is now withheld (the reaching read raises instead of returning
  it). Truncation is unchanged — its recoverable prefix is still delivered.
- **`close()` never raises a content fault.** Cleanup is safe; a content error can
  neither mask nor be masked by an exception unwinding a `with` body; safety does not
  hinge on `close()` running.
- **Detection, not prevention, in streaming mode.** Any bounded read returns some
  bytes before the final verdict is known; the guarantee is "you are told before you
  can conclude the member is complete-and-intact," not "you are told before you touch
  any bytes." Prevention is `VerificationMode.STRICT`.
- **Encrypted members are a sharper case.** With a single authentication tag over the
  whole member, streaming *must* release unverified plaintext (the classic
  release-of-unverified-plaintext problem); the tag is checked only when the member is
  read to its end. Segment-authenticated formats (per-chunk AEAD) could verify each
  chunk before release, but archivey's current formats do not. Threat models that
  cannot tolerate unauthenticated plaintext must use STRICT. The docs state this
  distinction explicitly even though the API surface treats CRC and auth-tag members
  the same.
- **Requires knowing "the end" on the reaching read.** Members with a declared size
  (most container members, e.g. ZIP) verify on the read that reaches that size.
  Size-unknown members (e.g. standalone gzip) rely on the decoder's in-band
  end-of-stream so corruption is still caught on the last data read, matching stdlib
  `gzip` — not deferred to a trailing empty read.
