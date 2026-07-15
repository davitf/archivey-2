## Context

WinZip AES (the AE-x spec) encrypts a ZIP member as: `[salt][pw_verify(2)]
[AES-CTR ciphertext][HMAC-SHA1(10)]`. The member's `compress_type` is 99; the AES extra
field `0x9901` holds `{vendor_version: AE-1|AE-2, vendor_id "AE", strength: 1|2|3 →
128|192|256, actual_compression_method}`. Key material is PBKDF2-HMAC-SHA1(password, salt,
1000 iterations) producing `enc_key(strength/8) ‖ auth_key(strength/8) ‖ pw_verify(2)`;
salt length is `strength/16` bytes (8/12/16). CTR uses a little-endian counter starting
at 1, no nonce. The stdlib gives PBKDF2 (`hashlib.pbkdf2_hmac`) and HMAC (`hmac`); AES-CTR
comes from the existing `[crypto]` backend. `zip-native-codec-streams` provides the raw
member-bytes slice + codec dispatch this composes onto.

## Goals / Non-Goals

**Goals:**
- Read AE-1 and AE-2 members (all three strengths) with a password, integrity-checked.
- Reuse `[crypto]` AES + the `zip-native-codec-streams` raw-data path — no new dependency.
- Correct AE-2 (no CRC) vs AE-1 (CRC) integrity/hash semantics.

**Non-Goals:**
- Writing AES ZIPs (writing phase).
- Traditional ZipCrypto changes (unchanged).
- A native central-directory parser (still stdlib `zipfile` for listing).

## Key decisions

- **Layer order on read:** raw slice → AE decrypt stage (verify pw_verify, AES-CTR,
  accumulate HMAC) → codec-layer decompress (actual method) → member stream. HMAC covers
  the *ciphertext*, so it is checked as bytes are consumed and finalized at EOF, parallel
  to how `VerifyingStream` finalizes digests — a mismatch raises at the terminal read.
- **Password verification is fast-fail.** The 2-byte pw_verify value is checked before
  streaming; a wrong password raises `EncryptionError` immediately (no bytes), consistent
  with the ZipCrypto path. This is weak (2 bytes) — same weak-check caveat as ZipCrypto; the
  HMAC is the strong check at EOF.
- **AE-2 hash semantics.** AE-2 sets the ZIP CRC to 0 and relies on the HMAC; so `crc32`
  is **not** surfaced for AE-2 members and no CRC verification runs (the HMAC is the
  integrity signal). AE-1 keeps and verifies the CRC in addition to the HMAC. This must be
  reflected in the stored-digest parity sweep (an AE-2 member legitimately has no `crc32`).
- **`[crypto]` gating.** The AES-CTR primitive requires `[crypto]`; absent it, an AE
  member raises `PackageNotInstalledError` (PBKDF2/HMAC alone can't decrypt). Detection of
  "this member is AES-encrypted" still works without `[crypto]` so the error is honest.
- **Strength coverage.** Support 128/192/256; the salt/key lengths derive from the
  strength byte, so all three fall out of one code path.

## Open questions (resolved during apply)

- Always finalize HMAC (pw_verify is only a cheap early-out) — confirmed.
- Fixtures: `7z a -tzip -mem=AES256` emits AE-2; AE-1 covered by a hand-built
  fixture helper in `tests/test_zip_aes.py` (skip-when-absent for the 7z path).
- Multi-candidate password flow: AE pw_verify is the per-candidate weak check via
  the existing `_PasswordCandidates.attempt` path; confirmed by
  `test_aes_multi_password_selects_winner`.
