## Why

Decompressed-content verification (CRC/digest, declared length, and encrypted
members' authentication tags) is **verify-as-you-go**: the verdict fires from the
read that completes the stream, and a partial read, a seek off the sequential
frontier, or a read-then-close **abandons** verification with no verdict. That is
a deliberate streaming / perf-budget choice, but it means integrity is **not
guaranteed for every access pattern** — a caller can consume unverified (and for
encrypted members, unauthenticated) bytes and get no error.

Two inconsistencies make this sharp:

- **Encrypted members break the rule in the *strict* direction.** WinZip AES
  (`WinZipAesDecryptStream.close`) drains the remaining ciphertext and verifies
  the trailing HMAC **on `close()`**, raising `CorruptionError` from close even
  for a partial-read caller. That surfaces a content fault from close (the very
  thing `compressed-streams` now forbids), verifies partial-read abandons that a
  CRC member would not, and makes the same access pattern behave differently by
  format — a "no surprises" violation.
- **Digest/CRC members break it in the *lax* direction.** A caller that reads a
  fixed prefix, or seeks, then closes gets no verdict — acceptable for streaming,
  but there is no opt-in for "verify everything regardless of how I read it,"
  which is exactly what extracting untrusted archives wants.

The uniform, honest fix is a **verification mode**: a consistent streaming
default (no content fault ever from `close`, for CRC *and* AES alike) plus an
opt-in strict mode that guarantees verification independent of access pattern.

Depends on `gzip-zlib-truncation-recovery` (the "content faults raise from read,
never from close" requirement and the `finish_on_close` teardown-only close).

## What Changes

- **Default (`STREAMING`) made uniform.** Content verdicts (digest, length,
  auth-tag) fire only from the completing read; partial read / seek-off-frontier /
  read-then-close abandon with no verdict; `close()` never surfaces a first
  content fault — **including encrypted members**. The WinZip AES close-drain is
  removed from the default path (the HMAC still fires on a full read, when the
  authenticating bytes are consumed).
- **New opt-in `VerificationMode.STRICT`** on `ArchiveyConfig`. STRICT guarantees
  a verdict regardless of access pattern, uniformly for CRC/digest *and* encrypted
  auth tags:
  - a member whose integrity cannot be confirmed never returns bytes that are
    silently trusted — STRICT verifies the whole member (decompress/decrypt-ahead)
    and raises before/at the point the unverifiable bytes would be handed out;
  - a seek that would disable frontier verification first forces a full verifying
    pass (or fails the seek) rather than silently dropping the check;
  - `close()` after a partial read completes verification (drain + verdict).
- **Honest cost signal.** STRICT can force a full decompress/decrypt ahead of use
  and therefore **breaks the ≤~1.3× budget by design**; the cost model / docs say
  so, and the mode is never the default.
- **Encrypted-member policy is a mode, not a per-format special case.** "Always
  authenticate" becomes STRICT behavior applied uniformly, not a gzip-vs-AES
  surprise baked into one backend's `close`.

## Capabilities

### New Capabilities

<!-- none -->

### Modified Capabilities

- `compressed-streams` — verification timing becomes a documented **mode**:
  a uniform streaming default (no content fault from `close` for digest *and*
  auth-tag members; abandon = no verdict) and an opt-in `STRICT` mode guaranteeing
  a verdict across partial reads, seeks, and close, with an honest cost signal.

## Impact

- Modules: `config.py` (`VerificationMode` enum + `ArchiveyConfig` field);
  `internal/streams/verify.py` (`MemberVerifier` mode plumbing);
  `internal/zip_aes.py` (`WinZipAesDecryptStream.close` no longer drains/authenticates
  in the default mode); the fused `ArchiveStream` path; possibly the seek path that
  disables verification.
- Public API: new `VerificationMode` + `ArchiveyConfig.verification_mode`
  (default `STREAMING`); no change to the streaming default's observable behavior
  except that encrypted members no longer raise a content fault from `close` in
  the default mode.
- Deps/extras: none.
- Tests: default — AES partial-read-then-close is quiet, full read still
  authenticates; STRICT — partial read of a corrupt/tampered member raises,
  seek-then-read still verifies (or the seek fails), close completes verification;
  parity across CRC and AES.
- Docs: `costs` (STRICT breaks the budget), `safe-extraction` (when to use STRICT),
  `usage` (the config knob), a decision note.
