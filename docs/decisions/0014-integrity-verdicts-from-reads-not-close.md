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
  and finds damage raises (returning no bytes for that call); `close()` never raises a
  content fault. Same *failure surface* as stdlib `zipfile` / `gzip` (fault on read,
  not close) — not byte-identical timing (their large-read / EOF shapes differ).
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

**Corruption vs. truncation (labels, not proofs).** These are not the same *reported*
failure, and the labels are often **best-effort guesses** about what went wrong:

- **`TruncatedError` / a short return** means the stream ended before the declared size
  (or before a clean decoder end) — the member appears *incomplete*. The bytes already
  returned are a **salvageable prefix on a best-effort basis**: a corruption that still
  produces a valid-looking shorter decode is easily mistaken for truncation, so we
  **cannot guarantee** that prefix is bit-correct relative to the author's intent.
- **`CorruptionError`** means we observed positive evidence of wrongness — a checksum /
  auth mismatch, an over-run past the declared size, or a mid-stream structural decode
  failure. Withholding the final chunk of a member whose checksum just failed is still
  right: those bytes are known-bad. When only a checksum mismatch is known, prior
  chunks are also untrustworthy as a whole (the digest covers them).

So "deliver the apparent prefix before failing" is right for the *truncation label* and
pointless for a known checksum / auth failure. Option A is correct *for corruption*;
truncation-shaped failures naturally serve their apparent prefix and fail on a later
read. Callers that need cryptographic certainty use `VerificationMode.STRICT` (and
still treat exception labels as labels).

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
before the end deliberately opted out of checksum / auth verification.

Truncation is observed only when a read **asks for more than the decoder can still
produce**. If only 110 bytes are available of a `size=500` member, `read(109)` and
`read(110)` both succeed with a full-count return and no error — the caller never
tried to go past what exists (a prior pass that discovered the available length and
then rereads exactly that far is intentional and correct). `read(111)` is the first
call that crosses the cut: it returns a short 110, and a following read raises
`TruncatedError`. The same cut relative to the *declared* size is what
`read(member.size)` reports via `len < size`. Stopping short of both the declared size
and the available decoder output (e.g. `read(50)` then `close()`) is an early stop: no
checksum verdict, no truncation verdict, `close()` stays silent on content.

## Decision

### Full-count `read` — a committed contract, not an accident

archivey's public stream `read(n)` (for `n ≥ 1`) is **full-count**: it returns
**exactly `n` bytes** unless it reaches a terminal boundary — clean end-of-stream,
truncation, or a raised content error. It does **not** return short on healthy,
non-terminal data (unlike a raw `io.RawIOBase.read`; like `io.BufferedReader`). Two
consequences the rest of this ADR depends on:

- a **short return is a terminal signal** — clean EOF, or a truncation-shaped prefix;
  never "healthy data, ask again";
- **`read(member.size)` reaches the declared size in one call**, which is what makes it
  a verifying event and closes the motivating regression.

This is a **library-wide contract delta**, not something already true everywhere. Today
`DecompressorStream.read` coalesces, but `MemberVerifier.read` issues a single
`inner.read(want)`, and an unverified `ArchiveStream` passes `read(n)` straight to its
inner (e.g. ZIP's `ZipExtFile`). Adopting this ADR makes full-count part of the
`compressed-streams` stream contract: **every public `read(n)` path must coalesce to
`n`-or-terminal** (or document an explicit exception). `read(0)` is a no-op, never EOF.
The contract binds the public `ArchiveStream` / codec-stream surface; arbitrary *inner*
`BinaryIO` objects need not be full-count — the wrapper coalesces over them.

### Where the verdict fires

Content-integrity verdicts (stored checksum / digest, encrypted-member authentication
tag, truncation, and declared-size over-run) are delivered from a **`read()` call,
never from `close()`**. The **target contract** is uniform across formats:
`close()` is teardown-only and does **not** raise a content fault
(`CorruptionError` / `TruncatedError` / `EncryptionError` from verification or
backend finalization).

**Today that target is best-effort; the intention is complete parity.** The verifier
path already meets it. Two known gaps remain and are **debt to close**, not blessed
carve-outs:

- **WinZip AES** drains and verifies its HMAC in its own `close()` — remove
  (`verification-integrity-mode` Decision 2).
- **`unrar` / subprocess backends** may only surface wrong-password or CRC exit codes
  when the process is reaped on close. Work toward parity by **eagerly finalizing** the
  underlying resource as part of the *completing* read (after the last content byte,
  reap / close the inner so the fault raises on that `read()`), and on an *early-stop*
  `close()` **suppress** content-class faults that arrive only from teardown (the
  caller opted out of a completing read). Until those land, a content error from
  `close()` on those paths is a known inconsistency, not the contract.

`close()` MAY still propagate a true resource teardown signal (`OSError` closing a
file descriptor, and similar non-content failures).

A member is verified at the moment a read **reaches its end** — whichever comes first:

- the read that consumes the member's **declared** size, or
- the read that reaches the **decoder's end-of-stream** (its in-band end marker, or the
  underlying source returning `b""`).

At that moment:

- **Corruption** (checksum / auth mismatch, or a mid-stream structural error): the
  reaching read raises `CorruptionError` and returns no bytes. The final chunk of a
  member known to be corrupt is **withheld** for **size-declared** members. For
  **size-unknown** members (e.g. standalone gzip, where the CRC lives in a trailer
  *after* the last plaintext byte), the verdict surfaces on the read that **observes
  end-of-stream** — possibly after the final data chunk was already released. We do
  **not** require a one-chunk delayed-release lookahead for uniform withhold timing;
  `read(-1)` / read-to-`b""` still honors the top-level guarantee, and
  "detection, not prevention" already permits pre-verdict bytes.
- **Over-run** (decode output exceeds the declared size — a corrupt length field): the
  read that would cross the declared size raises `CorruptionError`, **independent of
  the checksum** (the over-run itself is the corruption, even if the hash-so-far would
  pass). This is the same stop-at-declared-size → `CorruptionError` bound that
  `gzip-zlib-truncation-recovery` already specifies as the decompression-bomb cap.
- **Truncation / short** (decoder end-of-stream before the declared size, or an
  incomplete decode): every byte the decoder could produce was delivered on the
  preceding full-count reads; the first read that **asks past** the available output
  returns the remaining prefix (a **short return**, not an exception), and the *next*
  read raises `TruncatedError`. A read that requests *exactly* the remaining available
  bytes succeeds with a full-count return and no error.

Reaching the declared size is **always** a verifying event — checksum *and* over-run —
regardless of how the caller got there (`read(n)` with `n == remaining`, or a chunked
loop). The verdict is a property of *reaching the end*, not of the call shape.

A read that **stops before the end** — at any offset short of both the declared size
and a past-available attempt — produces **no verdict**, and `close()` stays silent on
content (modulo the best-effort gaps above).

### Seek: keep length checks; lose only the checksum

A seek off the sequential frontier breaks *incremental hashing* (non-linear
consumption), so the **checksum** verdict is best-effort and forfeited for the rest of
that handle's life — a seek-forward-then-back-to-end must not re-enable a hash
comparison over a non-contiguous byte range (false-positive `CorruptionError`). The
**length / structural** verdict is position-based, not hash-based: at EOF the stream
knows its total decompressed position, so **truncation** (short of the expected size)
and **over-run** (past it) stay detectable and SHALL still raise, even after a seek.
A member that is not *truly* seekable — one satisfied by rewinding / re-decoding from
the start — preserves linear hashing, so the checksum is not lost there either.
Honest scope: **checksum detection is best-effort, forfeited only on a genuine
intra-stream seek of a truly-seekable member (itself an opt-in capability); length /
truncation / over-run detection is always on.**

### Short returns are never *known*-corrupt bytes

Because a checksum / auth mismatch *raises* rather than returning short, a short
return from a full-count `read` is only ever **clean-but-complete** (you asked for more
than the member holds) or **truncation-shaped** (an apparent incomplete prefix). It is
never "here are bytes we already know fail the digest." A single-call reader
distinguishes the two by length:

- size-declared member: `len(data) == member.size` → complete and checksum-verified;
  `len(data) < member.size` → truncation-shaped (the shortfall is the tell), and
  reading again raises `TruncatedError`;
- size-unknown member: a bare `read(n)` cannot self-certify — read `-1` or to `b""`.

The truncation-shaped prefix is **best-effort salvage**, not a proof of correctness
(see Context: corruption can look like truncation).

### `read_exact(n)` — rejected

A dedicated `read_exact` would kill the sized-read truncation footgun in one call, but
it adds a **non-standard method** to the stream surface. A core goal is that code
written against archivey streams also works against ordinary file objects (and vice
versa), so we do **not** add methods a plain `BinaryIO` lacks. The truncation-short
residual is handled with **standard means** instead: the guaranteed whole-member
idioms are `read()` / `read(-1)` / iterate-to-`b""` (all standard, all raise on
truncation-shaped failure); `read(member.size)` is a *bounded* read whose short return
the caller checks by length; and `extract()` / whole-member helpers use a completing
read.

### Diagnostics — raise vs advisory

This ADR does not invent a new diagnostic taxonomy; it draws the line against the
existing `diagnostics` model (`DIGEST_UNVERIFIABLE`, rewind / seek-index advisories as
data; digest mismatch and truncation as hard errors).

**Rule of thumb:** **raise** when the library knows the member is damaged (or
over-ran). **Diagnose** when the library could not or did not finish a check, or
*hid* a backend signal — especially when silence would look like success. Never demote
an integrity verdict to a diagnostic and hope `RAISE` disposition will stand in for
`CorruptionError` / `TruncatedError`.

| Situation | Raise? | Diagnostic? |
| --- | --- | --- |
| Checksum / auth mismatch, over-run, mid-stream decode failure | `CorruptionError` | No |
| Read past available / completing read short of declared size | `TruncatedError` (or short, then next read) | No |
| Expected digest present but algorithm / hasher unavailable | No (bytes still flow) | **Yes — already** `DIGEST_UNVERIFIABLE` |
| Early stop / partial read then `close()` | No (by design) | **No** (default) — deliberate opt-out; diagnosing every peek is noise |
| Seek forfeits checksum for the handle | No | **Yes — add** (name TBD, e.g. `CHECKSUM_FORFEITED_AFTER_SEEK`): once per handle when a true intra-stream seek drops incremental hashing; same honesty family as `STREAM_REWIND_REDECOMPRESSES` / `SEEK_INDEX_DEGRADED` |
| Early-stop `close()` suppresses a teardown-only content fault (`unrar` / AES-style parity path) | No (swallowed for close parity) | **Yes — add** (name TBD, e.g. `CONTENT_FAULT_SUPPRESSED_ON_CLOSE`): backend knew something; swallowing without a trace is the surprise |
| Completing read surfaces that fault via eager finalize | Exception on that `read()` | No extra diagnostic |
| Streaming released bytes before a later CRC / auth fail | Later `CorruptionError` | No (default) — detection-not-prevention is the mode |
| `read(member.size)` short; caller ignores length | No | No — idiom + length check; diagnostics are not a `len` substitute |
| “Could be corruption mislabeled as truncation” | Still `TruncatedError` | No for now — documented caveat; a confidence field waits until something consumes it |
| Every encrypted member under STREAMING | No | No — accepted default posture; STRICT is the opt-in |

The two new codes are **follow-ups** (emitter + `diagnostics` / OpenSpec delta when the
seek-forfeit and close-parity paths land) — not blockers for accepting this ADR or for
resuming #183. Exact code names are TBD at implementation.

## Guarantee (for users)

> **Read a member to its end: a corrupt member raises `CorruptionError` on a `read()`;
> a truncated member raises `TruncatedError` or returns short of its declared size; a
> clean member returns all its bytes, checksum-verified. Stop before the end and it is
> not verified. `close()` never raises a content error (target contract; best-effort
> today on a few backends — see Decision).**

"To its end" means `read(-1)` / `readall`, reading until `read()` returns `b""`, or —
for a member with a **declared** size — reading that many bytes. For that whole-member
read, each outcome tells you what the library claims — with the honesty caveats below:

- a **`CorruptionError`** means we have positive evidence of wrongness — **discard
  everything read from this member; none of it is trustworthy** as a complete intact
  member (the raising call returns nothing). A digest / auth mismatch is the clear
  case; mid-stream structural failures are likewise treated as untrustworthy;
- a **`TruncatedError`** means the member appears **incomplete** — the bytes already
  returned are a **best-effort salvageable prefix**, not a proven-correct prefix
  (corruption that decodes to a shorter stream is easily labeled truncation). The
  raising call itself returns nothing; do not treat the prefix as the whole member;
- a **full-length** return (`len == member.size`, or a subsequent `b""`) means the
  content was **checksum-verified** — trust it under the digest's strength;
- a **short** return (`len < member.size`, no exception) means **truncation-shaped** —
  an apparent incomplete member; **"no exception" does not mean "complete."** Check
  the length, or read again to get the `TruncatedError`. Prefix correctness remains
  best-effort.

Corruption that the library can *prove* (digest / auth mismatch, over-run) is caught
whenever such a read reaches the end — independent of whether `close()` is ever
called. Callers who must verify **regardless** of access pattern (partial reads,
seeks, or "never release unverified bytes") use `VerificationMode.STRICT`
(`verification-integrity-mode`), which fully verifies a member before returning any of
it.

### Call × failure matrix (size-declared member)

Assume a member truncated after 110 decompressed bytes with `member.size == 500`:

| Call | Corrupt at full length | Truncated after 110 of 500 |
| --- | --- | --- |
| `read(109)` (from start) | (n/a — not yet at end) | returns 109, **no error** (did not ask past available) |
| `read(110)` (from start) | (n/a — not yet at end) | returns 110, **no error** (exactly available) |
| `read(111)` (from start) | (n/a) | returns short 110; following `read()` raises `TruncatedError` |
| `read(member.size)` | raises `CorruptionError` | returns short (`len < size`), **no exception** |
| `read(-1)` / `readall` | raises `CorruptionError` | raises `TruncatedError` |
| chunked until `b""` | raises on the read that reaches the size (withholds that chunk) | delivers the whole prefix; first read *past* available returns short; the next raises `TruncatedError` |
| partial read, then `close()` | quiet (early stop) | quiet (early stop) |

The load-bearing asymmetry: **`read(member.size)` raises on corruption but returns a
short buffer on truncation** — because a known digest failure yields wrong bytes
(withheld) while a truncation-shaped end yields an apparent incomplete prefix
(delivered). This is a **deliberate idiom** ("return the available prefix; raise on
known-wrong bytes"), not a trap — see *`read(member.size)` — read the available prefix
without raising* below. Size-unknown members have no `member.size` to read to, so a
bare `read(n)` cannot self-certify at all — use `read(-1)` / read-to-`b""`.

## Full-count `read`: rationale and trade-offs

Of everything in this ADR, the full-count `read` commitment has the **broadest blast
radius** — it changes a cross-cutting stream contract, not just the verifier — so it
gets its own accounting. The two candidate contracts:

- **up-to-`n`** (raw `io.RawIOBase` semantics): `read(n)` may return *any* number of
  bytes `1..n` on healthy data; only `b""` means end. A stream is free to "decode one
  compressed block and return whatever it yielded."
- **full-count** (`io.BufferedReader` / `BytesIO` / on-disk file semantics): `read(n)`
  returns *exactly* `n` bytes unless it reaches a terminal boundary (EOF, truncation,
  or a raised content error). We adopt this.

### Why full-count

- **It matches the file object users actually hold — the strongest reason.** The
  dominant binary file object in Python is `open(path, 'rb')`, an `io.BufferedReader`,
  and **`BufferedReader.read(n)` is full-count**: it issues multiple underlying reads to
  return exactly `n` unless EOF. So `data = f.read(known_size)` is a *correct, everyday*
  idiom, and an archivey stream is the thing people most often swap in for a file. The
  real question is therefore not "full-count vs. the stream contract" but **which
  standard we match** — `BufferedReader` (full-count) or raw `io.RawIOBase` (up-to-`n`).
  Matching `BufferedReader` makes archivey a **drop-in for file-reading code**; matching
  `RawIOBase` silently breaks it. Full-count is the *more* compatible choice, not less —
  and file-reading code is the single most likely thing to be pointed at our streams.
- **It keeps naive single-call reads safe on healthy data.** Under up-to-`n`, a stream
  free to "decode one block and return it" can hand back a *partial* buffer on a
  perfectly good member; a caller who reads once and trusts it **silently loses correct
  data on an undamaged archive** — the worst outcome, and a frequent one. Full-count
  eliminates it: a healthy `read(n)` always returns `n`. Its only silent-ish residual is
  genuine truncation-shaped ends, where the shortfall is length-checkable.
- **It makes the verification story true.** Under up-to-`n`,
  `data = f.read(member.size)` can return a prefix on healthy data; the single-call
  caller never reaches the declared size, and corruption is silently accepted — the
  exact regression this ADR closes. Only full-count guarantees `read(member.size)`
  reaches the end and therefore verifies.
- **Simpler contract, simpler docs.** "You get what you asked for; you get less only at
  the end (EOF or truncation)." One sentence replaces "a short read might mean
  anything," and a short return regains a single meaning: a terminal boundary.
- **Reviewers converged on it** as the right load-bearing fix once the sized-read
  guarantee was on the table.

### What it costs

- **A stream can no longer "read one block, decompress it, return those bytes."** Every
  public `read(n)` must **coalesce**: loop-decode until it holds `n` output bytes or
  hits a terminal boundary, and **buffer any overflow** (a block that yields more than
  `n`) for the next call. `DecompressorStream` already works this way; but
  `MemberVerifier.read` (a single `inner.read(want)`) and an unverified `ArchiveStream`
  passthrough (straight to `inner.read(n)`) do **not** — they must be updated. This is a
  **`compressed-streams` contract delta across backends**, not a local verifier tweak.
- **More work inside a single call for large `n`.** `read(n)` may decode several blocks
  before returning. The work is still **bounded by `n`** (the caller's output budget),
  so the `max_length` output cap and decompression-bomb bounds are unaffected — the same
  bound `BufferedReader` operates under.
- **Latency shift on pipe / non-seekable sources.** `read(n)` may wait for enough input
  to fill `n` rather than returning what is immediately available. Archives are almost
  always seekable files, so this is minor; a caller wanting incremental low-latency
  delivery can pass a small `n`.
- **A small buffer + fill loop in each wrapper.** Already carried by the decompressor
  engine; the cost is extending it to the verifier and passthrough paths.

**Verdict: worth it.** The cost is concentrated in wrapper plumbing that the
decompressor path already has, and the payoff is a simpler, `BufferedReader`-compatible
contract that makes the whole read-to-end verification guarantee — and the honest
`read(member.size)` idiom — actually true.

### `read(member.size)` — read the available prefix without raising

The worry with full-count is that `read(member.size)` on a **truncated** member returns
a short buffer with *no exception*, so a caller who neither checks the length nor reads
again never learns it was short. Rather than treat that as a trap to mitigate, we make
it a **deliberate, first-class archivey idiom** with a clear meaning:

> **`read(member.size)` = "give me the member's content; on a *truncation-shaped*
> member return the available prefix instead of raising."**

It still raises `CorruptionError` on *known-wrong* bytes (reaching the declared size
with a bad checksum), so it means **"return what is available; raise when we can prove
wrongness,"** never "return bytes we already know fail the digest." It is not a
convenience that hides damage: there is **no equivalent pattern for a plain file** — a
file has no member with a declared size — so `read(member.size)` appears only in
archivey-aware code, written by someone who already knows what a member size *is*, not
ported by habit from file-reading code. That is exactly why the sized-read asymmetry is
safe to expose: the people who write it are the people who understand it.

This is a **narrow first step** toward damaged-input salvage (VISION / `IDEAS.md`), not
the full salvage mode (e.g. recovering a truncated ZIP that lost its central
directory). No claims beyond sized-read / streaming prefix delivery for
truncation-shaped member bodies.

A caller who needs to know whether the member was whole has two standard ways — **no
extra method required**:

- **Length check** (cheap, weaker): `len(data) == member.size` → complete;
  `len(data) < member.size` → truncation-shaped.
- **A following `read()`** (authoritative for the *label*): on a healthy member it
  returns `b""` (you are at verified EOF — the checksum was already checked when the
  sized read reached the declared size); on a truncation-shaped member it raises
  `TruncatedError`.

So the whole-member idioms line up by **intent**, all using only standard `BinaryIO`
methods:

- `read()` / `read(-1)` / iterate-to-`b""` — *read the whole member and verify*; raise
  on **any** damage label (corruption or truncation);
- `read(member.size)` — *return the available prefix*; raise only on known corruption,
  return short on truncation-shaped ends, disambiguated by a length check or a
  following `read()`.

Either way, **known corruption is never silent.** Against up-to-`n` this is a strict
improvement: up-to-`n` would return a short buffer even on *healthy* data (silently
losing good bytes) and could accept corruption silently on a single call; full-count
does neither.

## Open questions

1. **Encrypted members — default posture, constrained by what STRICT can afford.**
   Streaming releases unauthenticated plaintext before the tag is checked — sharper than
   a bad CRC, and in tension with VISION's "no silent success." But "verify before
   releasing any plaintext" is **not free, and its cost depends on the member**, which
   is why a blanket `STRICT` default is not simply "buffer everything":
   - **Compressed + encrypted:** a wrong password almost always yields plaintext the
     decompressor rejects almost immediately (an early `CorruptionError`), so streaming
     already fails fast **without buffering**. (Confirm against the existing
     wrong-password tests.)
   - **Stored (uncompressed) + encrypted:** wrong-password plaintext is just bytes — no
     decompressor to reject it — so the authentication tag at the end is the **only**
     detector. This is the real streaming exposure.
   - **Very small members:** the stream may end before a decompression error can
     surface, so the tag is again the only tell.

   So a memory-safe "authenticate before release" cannot buffer arbitrarily. The viable
   shapes per member are: **buffer in memory only up to a bounded size**; **decrypt
   twice** (verify pass → rewind → re-decrypt) for a **seekable** stored member (cheap
   for a local file, costly over a network); and **refuse to stream / require an explicit
   opt-in** for a **non-seekable** stored member above the buffer bound. Decision: keep
   STREAMING the default (accepting the stored / tiny residual, which this ADR still
   surfaces on the completing read / `read(-1)`), or default authenticated members to
   STRICT with the per-shape strategy above? **Leaning: STREAMING default** — compressed
   wrong-password fails fast and a blanket STRICT is not implementable memory-safely — but
   document the stored/tiny exposure sharply and make STRICT the easy opt-in.

## Consequences

- **Resolves the open question that parked `gzip-zlib-truncation-recovery` (#183).**
  This ADR exists *because* #183's review surfaced the read-vs-close / sized-read hole;
  the #183 implementation is on hold pending it. #183's earlier Decision 8 — a bounded
  `read(n)` on a digest mismatch delivers every byte and raises on the terminal empty
  read ("do not withhold the last data chunk";
  `test_verify_mismatch_raises_at_eof_without_losing_final_chunk`) — is **revised** by
  this ADR for the corruption case: the read that reaches the declared size (or decoder
  EOS) raises and **withholds** that chunk for size-declared members. When #183 resumes,
  its delta and that test are updated to the withhold-on-reaching-read rule so the two
  texts agree — not a live conflict, but the settlement that unblocks #183.
  **Truncation-shaped delivery is unchanged** — #183's recoverable-prefix delivery
  stands (with the best-effort correctness caveat above). Size-unknown timing is
  decided: verdict on the EOS-observing read, no mandatory lookahead withhold.
- **`read(member.size)` on a corrupt size-declared member raises from that read** and
  returns nothing — closing the silent-acceptance regression, without depending on
  `close()`.
- **High-level helpers read to the true end.** `extract()` and any whole-member helper
  MUST consume each member with a *completing* read (`read(-1)`, or a sized read plus a
  drain to `b""`), so the default extraction path raises `TruncatedError` on a short
  member rather than silently writing a truncated file. The non-raising short return is
  the deliberate `read(member.size)` **prefix** idiom for callers who opt into it,
  never the default `extract` behavior.
- **STRICT is proposed, not shipped (sequencing).** The escape hatch for "never release
  unverified / unauthenticated bytes" is `VerificationMode.STRICT`
  (`verification-integrity-mode`, still proposed). This ADR's STREAMING default is
  accepted now; STRICT is sequenced after. Until it lands, STREAMING —
  detection-not-prevention — is the only posture, so **encrypted members stream
  unauthenticated plaintext by default** in the interim.
- **Full-count `read` becomes part of the `compressed-streams` contract** — a
  cross-backend delta, not a local tweak (every public `read(n)` path must coalesce to
  `n`-or-terminal). See *Full-count `read`: rationale and trade-offs* for the full
  accounting; the short version is that the decompressor engine already does this and
  the verifier / passthrough paths must be brought in line.
- **`close()` never raises a content fault — target contract, best-effort today.**
  Cleanup is safe when the target holds; a content error can neither mask nor be masked
  by an exception unwinding a `with` body; safety does not hinge on `close()` running.
  AES HMAC-on-close and unrar-exit-on-close are documented debt toward that parity
  (eager finalize on completing read; suppress teardown-only content faults on
  early-stop close).
- **Detection, not prevention, in streaming mode.** Any bounded read returns some bytes
  before the final verdict; the guarantee is "you are told before you can conclude the
  member is complete-and-intact," not "you are told before you touch any bytes."
  Prevention is `VerificationMode.STRICT`.
- **Size-declared vs. size-unknown timing differs** (decided). Size-declared: verdict
  on the read that reaches the declared size (the decoder must also consume the
  trailing CRC/tag to validate before that read returns or raises). Size-unknown:
  verdict on the read that observes end-of-stream — not a mandatory lookahead withhold
  of the final data chunk.
- **Encrypted members are a sharper case** (open question 1). With a single tag over
  the whole member, streaming *must* release unverified plaintext; segment-authenticated
  (per-chunk AEAD) formats could verify each chunk before release, but archivey's
  current formats do not.
- **Exception labels are best-effort trust signals.** `CorruptionError` ⇒ positive
  evidence of wrongness; trust **nothing** already read from this member as intact.
  `TruncatedError` ⇒ the member appears **incomplete**; the bytes already returned are
  a **best-effort** salvageable prefix (not proven correct — corruption can look like
  truncation). Documented on the exceptions and in the streaming guide.
- **Narrow salvage, not full salvage.** Sized-read / streaming prefix delivery for
  truncation-shaped bodies is a first step toward VISION's damaged-input salvage story;
  it makes **no** claims about recovering archives that lost structural metadata (e.g.
  truncated ZIP without a central directory).
- **Diagnostics stay advisory; integrity verdicts stay exceptions.** Early stops and
  STREAMING pre-verdict byte release are silent by design. Follow-up codes (when those
  paths land): checksum forfeited after seek, and content fault suppressed on
  early-stop close — so capability loss and swallowed backend signals stay queryable.
  `DIGEST_UNVERIFIABLE` is unchanged. See *Diagnostics — raise vs advisory*.

## Implementation notes

- **Trailer consumption on exact-size reads.** For a size-declared member,
  `read(member.size)` must pull the underlying CRC/auth trailer and validate *before*
  the call returns or raises — the read that reaches the declared size finalizes the
  hash.
- **Seek-state tracking.** Once a seek off the sequential frontier disables the
  checksum verdict, it stays disabled for that handle; length / over-run / truncation
  checks remain active.
- **Full-count coalescing.** Bounded `read(n)` on the verifier/wrapper must loop the
  inner stream until it has `n` bytes or a terminal boundary, so the full-count
  contract holds even over a short-reading inner `BinaryIO`.
- **Eager finalize on completing reads (parity path).** Where a backend only reports
  content faults at process/resource teardown (`unrar` exit codes, similar), the
  completing-read path should reap/finalize immediately after the last content byte so
  the fault raises on that `read()`; early-stop `close()` should not promote those
  teardown-only content faults into the caller's close — emit the suppressed-fault
  diagnostic instead (when that code lands).
- **Seek-forfeit diagnostic.** When a true intra-stream seek disables the checksum
  verdict for the handle, emit the forfeited-checksum diagnostic once; length /
  over-run / truncation checks remain active without a diagnostic of their own.
