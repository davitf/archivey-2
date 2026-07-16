# Maintainer decisions — Brief 2 (crypto) — RESOLVED

Answers below fold in davitf's PR #115 review (2026-07-15) plus a check of the `archivey-dev`
reference at `730275b`. Open source-verification items are collected in
`7z-source-questions.md`.

## Q1 — RAR5 tweaked-checksum BLAKE2sp (F1) — **decided: untweak-and-verify**

davitf: *"DEV had special handling for tweaked rar checksums (maybe only crc32). If we have
password(s) at open time, could we quickly check which one is correct using the check field,
then derive the correct checksums from the tweaked ones … if we don't know the password,
leave the hashes empty (maybe store the tweaked ones in extras)."*

Findings from `archivey-dev/src/archivey/formats/rar_reader.py`:

- DEV implements `convert_crc_to_encrypted` = RAR's `ConvertHashToMAC`. The tweak is a
  **one-way forward transform**, not reversible: `tweaked = f(hash_key, real_hash)`. So you
  **cannot** "derive the correct checksums from the tweaked ones" — the real CRC/BLAKE2sp is
  not recoverable from the stored tweaked value. What you *can* do (and what DEV does) is
  compute the real hash from decrypted data, apply the same forward transform, and compare to
  the stored tweaked value.
- Key derivations (all PBKDF2-HMAC-SHA256 over UTF-8 password + 16-byte salt):
  - AES key: `1 << kdf_count` iterations.
  - **HashKey** (the tweak/MAC key): `(1 << kdf_count) + 16`.
  - PswCheck: `(1 << kdf_count) + 32`.
- CRC32 transform: `d = HMAC-SHA256(HashKey, crc.to_bytes(4,"little"))`; XOR the eight
  little-endian uint32 words of `d` → 4-byte tweaked CRC.
- BLAKE2sp transform (per UnRAR `ConvertHashToMAC`, **flagged for source confirmation** in
  `7z-source-questions.md`): `tweaked32 = HMAC-SHA256(HashKey, real_blake2sp_32)` — the stored
  32 bytes are an HMAC of the real digest. **DEV implemented the CRC path only; it never
  untweaked BLAKE2sp** — it just dropped the tweaked crc32 (`crc32 = None`) so it never
  false-positived. v2 regressed by dropping crc32 but *keeping* the tweaked blake2sp.

**Plan (matches davitf's proposal):**
1. **Immediate correctness fix (unblocks reads):** make `_member_hashes` symmetric — drop
   `blake2sp` when tweaked, exactly as it already drops `crc32`. This alone removes the false
   `CorruptionError`. Store the tweaked values in `member.extra` (`rar.tweaked_crc32`,
   `rar.tweaked_blake2sp`) so nothing is lost.
2. **Full fix (restores verification when the password is known):** thread the confirmed
   password / HashKey into the RAR verification stage and verify by forward-transform (CRC32
   and BLAKE2sp). When no correct password is known at open time, leave `member.hashes` empty
   and keep the tweaked values in `extra`. This is strictly better than DEV (which did CRC
   only) and needs the BLAKE2sp transform confirmed against UnRAR source.

## Q2 — 7z folders with no integrity anchor (F2) — **decided: best-effort, don't hard-error**

davitf: *"I'd rather not hard-error if the data might actually be correct … extremely rare
… how does 7z itself handle it? doesn't zip have this problem as well … error detection is
best-effort."*

Agreed — retract the "fail closed" recommendation. Rationale:

- **7-Zip itself** relies on the folder/stream CRC to reject a wrong password; with no CRC it
  cannot detect a wrong password either and returns whatever the cipher produced. Matching
  that = best-effort. (Included as a source-confirmation item in `7z-source-questions.md`.)
- **ZIP is not actually exposed** the same way: WinZip AE-2 sets CRC=0 but authenticates with
  a **mandatory** HMAC (checked on close — verified good), and ZipCrypto STORED members are
  disambiguated by the central-directory CRC-32. So every ZIP encrypted member still has an
  integrity anchor (HMAC or CRC). 7z is the only format here where an encrypted member can
  legitimately carry **zero** anchor (AES+COPY, no digest, CRC-less members).
- The 1/256 ZipCrypto one-byte-check collision is a *different* best-effort case already
  handled by the multi-candidate CRC pass; it does not apply when there is no CRC at all.

**Plan:** keep the current accept-and-return behaviour, but emit a diagnostic
(`DIGEST_UNVERIFIABLE` or a new `DECRYPTION_UNVERIFIED`) when an encrypted folder has no
integrity anchor, so a caller can tell "decrypted but not verifiable" from "verified". **F2
downgraded to Low** (best-effort limitation, honest-signal gap — not silent-without-notice).

## Q3 — 7z KDF `NumCyclesPower`: real cap, user-selectable, max (F3)

What I can state now; the rest is in `7z-source-questions.md`:

- **Default:** 7-Zip encodes with `NumCyclesPower = 19` (2¹⁹ = 524 288 SHA-256 rounds,
  ~sub-millisecond).
- **User-selectable?** Not exposed in the 7-Zip GUI/CLI; the encoder is effectively fixed at
  19. The **format** stores it in the low 6 bits of the AES property byte, so a file can
  legally carry 0–0x3F regardless of what the official encoder emits.
- **Max / decoder cap:** the 7z format max is `0x3F`. Whether the official 7-Zip *decoder*
  caps `NumCyclesPower` (or loops `1 << value` unbounded, making 7-Zip itself DoS-able) is the
  key source question — see `7z-source-questions.md`.

Regardless of what 7-Zip does, v2 should apply its **own defensive cap**. Recommendation:
reject `NumCyclesPower > 24` (2²⁴ ≈ 16.7 M rounds, ~100 ms upper bound) with
`UnsupportedFeatureError` — well above any real archive (19) and mirroring the RAR5 KDF cap of
24 that v2 already enforces (`_RAR_MAX_KDF_SHIFT`). Confirm 24 or pick another ceiling.

## Q4 — questions for the 7-Zip / UnRAR source agent

davitf offered to run an agent over the 7-Zip (and we should add UnRAR) source. The full
checklist is in **`7z-source-questions.md`**.
