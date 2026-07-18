# Maintainer decisions — Brief 2 (crypto) — RESOLVED + APPLIED (#127)

Answers below fold in davitf's PR #115 review (2026-07-15), a check of the `archivey-dev`
reference at `730275b`, and the **7-Zip + UnRAR source answers** (2026-07-16, see
`7z-source-questions.md` for the raw citations). All questions are closed; the plans below
were implemented in **#127**.

## Q1 — RAR5 tweaked-checksum BLAKE2sp (F1) — **DONE (untweak-and-verify)**

The tweak is RAR's `ConvertHashToMAC` — a **one-way forward transform**, so we compute the
real hash from decrypted data, transform it, and compare to the stored value (we cannot
recover the real checksum from the tweaked one). Both transforms are confirmed against
UnRAR `crypt5.cpp`:

- **HashKey** = `PBKDF2-HMAC-SHA256(pw_utf8, salt, (1 << kdf_count) + 16)` — the AES key is at
  `1 << kdf_count`, HashKey at `+16`, PswCheck at `+32` (single PBKDF2 chain, three cutpoints;
  `crypt5.cpp:104,161`). Matches archivey-dev.
- **CRC32:** `d = HMAC-SHA256(HashKey, crc.to_bytes(4,"little"))`; fold the 32-byte digest into
  a 4-byte value by XOR of the eight little-endian uint32 words (UnRAR does it byte-wise with
  `Digest[I] << ((I & 3) * 8)`, equivalent). Matches archivey-dev's `convert_crc_to_encrypted`.
- **BLAKE2sp:** `tweaked32 = HMAC-SHA256(HashKey, blake2sp_digest[32])`, overwriting the 32-byte
  digest (`crypt5.cpp:206`). This is the path archivey-dev never implemented.
- **Gate:** the tweak is applied **iff** the file's `FHEXTRA_CRYPT_HASHMAC` (0x02) flag is set
  (`arcread.cpp:1080`, `extract.cpp:934`) — which is exactly what v2's `_crc_is_tweaked` already
  reads. Encrypted-header (`-hp`) archives do **not** auto-skip the tweak in the reader; unrar
  honours only the per-file 0x02 flag.

**Applied (#127):**
1. `_member_hashes` drops `blake2sp` when 0x02 is set (symmetric with crc32); tweaked values
   live in `member.extra` (`rar.tweaked_crc32` / `rar.tweaked_blake2sp`).
2. With a password, verify by forward-transform (CRC32 + BLAKE2sp) via
   `VerifyingStream(digest_transforms=…)` and `rar5_hash_key` / `convert_*_to_mac`.
   `member.hashes` stays empty when tweaked (plaintext digests are not recoverable from the
   stored MAC). Without a password, emit `DIGEST_UNVERIFIABLE` (`reason="tweaked_checksum"`).
3. Fixture `encryption_blake2sp.rar` + `tests/test_crypto_findings.py` cover the e2e path.

## Q2 — 7z folders with no integrity anchor (F2) — **DONE (best-effort + diagnostic)**

Source-confirmed: 7zAES has **no password check** of its own. 7-Zip's only wrong-password
gate is the extraction CRC when defined. With no CRC, store/copy members silently return
garbage.

**Applied (#127):** keep accept-and-return; emit `DIGEST_UNVERIFIABLE` with
`reason="no_integrity_anchor"` (reused existing code rather than adding
`DECRYPTION_UNVERIFIED`) for encrypted members whose folder has no digest and whose record
has no CRC. **F2 stays Low.**

## Q3 — 7z KDF cap + `0x3F` sentinel (F3) — **DONE (match 7-Zip)**

Source-confirmed in `7zAes.cpp`: accept `NumCyclesPower <= 24` or `== 0x3F`, else
`E_NOTIMPL`. The `0x3F` no-hash sentinel is real; counter layout matches.

**Applied (#127):** `parse_sevenzip_aes_properties` / `derive_sevenzip_aes_key` reject 25–62
with `UnsupportedFeatureError`. Folder password-confirm re-raises that (and
`PackageNotInstalledError`) instead of remapping to `EncryptionError`.

## Q4 — 7-Zip / UnRAR source checklist — **answered** (`7z-source-questions.md`)

All items A–D answered from source. D9 unlocked F4 below.

## F4 — **DONE (bare `-p` + stdin)**

**Applied (#127):** `open_unrar_p` appends bare `-p` and writes `password + "\n"` on
`stdin=PIPE` (keep `-p-` when no password). Tests spy on `Popen` (race-free vs
`/proc/<pid>/cmdline`).

## F5 — **DONE (`compare_digest`)**

**Applied (#127):** `_check_rar5_password` uses `hmac.compare_digest` for the check-blob
integrity prefix and the derived 8-byte PswCheck.
