## Context

Provenance: review of PR #183 (`gzip-zlib-truncation-recovery`). That change made
"content faults surface from read, never from `close`" the contract for decode +
verify streams, and made `MemberVerifier.finish_on_close` teardown-only. Two
things fall out of that and motivate this change.

### The default verification contract today

`compressed-streams` ("Decompressed output digests are verified at clean EOF"):
digests are computed incrementally and the verdict fires from the read that
completes the stream (terminal empty `read`, or `read(-1)`). **Abandon = no
verdict**: a partial read, a seek off the sequential frontier
(`note_seek` disables verification), or a read-then-close that never hits the
completing read consumes bytes without a verdict. This is a deliberate streaming /
≤1.3× perf choice — you only pay to verify what you fully read.

### The two asymmetries

| Path | Today | Problem |
| --- | --- | --- |
| CRC/digest member, partial/seek/close | No verdict (quiet) | Correct for streaming, but no way to *demand* verification |
| WinZip AES member, partial read then close | `close()` drains ciphertext + verifies HMAC, **raises `CorruptionError`** | Content fault from `close`; verifies an abandon a CRC member would not; per-format surprise |

`WinZipAesDecryptStream.close` (`zip_aes.py`) intentionally drains "so a short-read
caller still gets HMAC checked." That is a security instinct (don't hand out
unauthenticated AE-2 plaintext) implemented as a per-format `close` side effect —
exactly the inconsistency the mode below resolves.

## Goals / Non-Goals

**Goals:**

1. One **uniform** streaming default: verdict only from the completing read;
   partial/seek/close abandon with no verdict; `close()` never a first content
   fault — for digest **and** encrypted members alike.
2. An opt-in **`STRICT`** mode that guarantees a verdict regardless of access
   pattern (partial read, seek, close), uniformly for CRC/digest and auth tags.
3. Honest cost: STRICT may decode/decrypt-ahead and breaks the ≤1.3× budget by
   design; documented, never default.
4. Encrypted "always authenticate" expressed as a **mode**, not a backend-specific
   `close` behavior.

**Non-Goals:**

- Changing which algorithms are computable, or the `DIGEST_UNVERIFIABLE`
  diagnostic model.
- A per-member override in v1 (mode is archive-level `config`); revisit if needed.
- Verified random-access indexes — STRICT on a seekable member may simply force a
  full verifying pass rather than building a per-block MAC index.

## Assessment — is a mode overkill? (the maintainer's question)

**No — it is on-mission and it removes a special case rather than adding one.**

- **VISION fit.** "Safe by default," "damaged/hostile input is first-class," and
  "honest cost signals" all point at *offering guaranteed verification with a
  truthful price*, not at forcing it on every read (which would blow the perf
  budget) nor at hiding it per-format.
- **It resolves the AES inconsistency instead of entrenching it.** "Always
  authenticate encrypted content" becomes `STRICT`, applied uniformly to every
  integrity check, so the default stops being "CRC lax, AES strict-on-close."
- **The default stays cheap.** Streaming verification (verify what you fully read)
  keeps the ≤1.3× budget; STRICT is the opt-in for untrusted-archive extraction.

**Where the cost is real** (so it is *not* free, and should be its own change, not
folded into the gzip PR):

- STRICT on a **seekable** member must either verify-ahead (buffer / re-decode the
  whole member) or fail the seek — there is no cheap "verify a random-access read."
- STRICT must intercept the seek-disables-verification path and the partial-read
  path, which is real machinery in `MemberVerifier` / the fused `ArchiveStream`.
- Interaction with `extraction_limits` (a STRICT verify-ahead must still honor
  output caps / bomb bounds).

**Verdict:** worth doing, as a **separate** change. The *default-consistency* half
(remove the AES close-drain; `close` never a content fault, uniformly) is small
and unblocks the gzip PR's contract; the `STRICT` half is the larger, opt-in piece.

## Decisions

1. **`VerificationMode` on `ArchiveyConfig`.** `STREAMING` (default) and `STRICT`.
   Archive-level for v1 (mirrors `strict_archive_eof`, `extraction_limits`). Naming
   open (see Q1). **Rejected:** a bare `verify_strict: bool` — an enum leaves room
   for a future `OFF` (skip verification) or `EAGER` variant without a breaking flag.

2. **Default `STREAMING` is uniform across digest and auth tags.** Verdict only
   from the completing read; partial/seek/close abandon; `close()` never surfaces a
   first content `TruncatedError` / `CorruptionError`. **Encrypted members follow
   this too:** the WinZip AES HMAC still fires on a full read (the authenticating
   bytes are consumed), but the `close`-time drain is removed from the default
   path. **Rejected:** keep AES authenticating on close in the default — it is the
   inconsistency this change exists to remove.

3. **`STRICT` guarantees a verdict regardless of access pattern**, uniformly:
   - Partial read of a member whose integrity cannot yet be confirmed: STRICT does
     not silently hand out un-verifiable bytes — it forces a full verifying pass
     and raises on a corrupt/tampered member (subject to bomb/output caps).
   - A seek that would disable frontier verification: STRICT forces a full verifying
     pass first, or fails the seek with a typed error — never silently drops the
     check.
   - `close()` after a partial read: STRICT completes verification (drain + verdict)
     — this is the *mode's* behavior, applied to every integrity check, replacing
     the per-format AES close-drain.
   **Rejected:** STRICT that only tightens `close` (still lets a mid-stream seek
   skip verification) — that would leave a silent hole.

4. **Honest cost.** STRICT is documented as breaking the ≤1.3× budget (it may fully
   decode/decrypt ahead of use). The cost model / `costs` doc states it; STRICT is
   never selected implicitly.

5. **Sequencing.** Land Decision 2 (default-consistency: remove the AES close-drain;
   uniform quiet close) first — it is small and completes the gzip PR's contract.
   Decision 3 (`STRICT`) follows as the larger opt-in piece. Both can ship in this
   change or Decision 2 can be pulled into the gzip PR; see Q2.

## Open Questions

1. **Names.** `VerificationMode.{STREAMING, STRICT}` vs. `{LAZY, EAGER}` vs.
   `{AS_YOU_GO, FULL}`; field name `verification_mode`. Also: do we want a third
   `OFF` (skip verification entirely) now or later?

2. **Where does the default-consistency fix land?** In this change, or pulled into
   `gzip-zlib-truncation-recovery` (since it completes that change's
   "close never raises a content fault" claim for encrypted members too)? The
   `gzip` change's spec already says "close never a first content fault"; the AES
   close-drain is currently a live exception to it.

3. **STRICT on seekable members: verify-ahead vs. fail-the-seek.** Buffer/re-decode
   the whole member to verify (costly, but random access keeps working) or refuse a
   seek in STRICT with a typed error (cheap, but less capable)? Recommendation:
   verify-ahead when the source is seekable and within `extraction_limits`, else a
   typed error.

4. **Security nuance for encrypted members in STREAMING.** Removing the AES
   close-drain means a partial read of an encrypted member returns *unauthenticated*
   AE-2 plaintext with no error (same posture as unverified CRC). Confirm this is
   acceptable as the default (a partial read *cannot* authenticate anyway — the MAC
   is at the end), with STRICT as the answer for callers who must authenticate.

## Risks / Trade-offs

| Risk | Mitigation |
| --- | --- |
| STRICT silently blows the perf budget | Documented cost; never default; honest `costs` entry |
| Removing AES close-drain weakens default integrity | STREAMING default matches CRC posture; STRICT restores guaranteed auth uniformly; full reads still authenticate |
| STRICT verify-ahead as a new bomb surface | Reuse `extraction_limits` / output caps in the verify-ahead pass |
| Mode proliferation / config surface growth | Enum with a small, documented set; archive-level only in v1 |
