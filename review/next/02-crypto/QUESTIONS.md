# Maintainer decisions — Brief 2 (crypto) — RESOLVED (source-confirmed)

Answers below fold in davitf's PR #115 review (2026-07-15), a check of the `archivey-dev`
reference at `730275b`, and the **7-Zip + UnRAR source answers** (2026-07-16, see
`7z-source-questions.md` for the raw citations). All questions are now closed; the "plan"
lines are the agreed implementation direction.

## Q1 — RAR5 tweaked-checksum BLAKE2sp (F1) — **untweak-and-verify (both transforms confirmed)**

The tweak is RAR's `ConvertHashToMAC` — a **one-way forward transform**, so we compute the
real hash from decrypted data, transform it, and compare to the stored value (we cannot
recover the real checksum from the tweaked one). Both transforms are now confirmed against
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
  reads. **Correction to the earlier writeup:** encrypted-header (`-hp`) archives do **not**
  auto-skip the tweak in the reader; unrar honours only the per-file 0x02 flag (untweaked
  checksums under `-hp` are a *writer* choice to omit the flag). So no header-encryption
  special-case is needed — key off 0x02 alone.

**Plan:**
1. **Interim correctness fix:** make `_member_hashes` symmetric — drop `blake2sp` when 0x02 is
   set, exactly as it already drops `crc32`; stash both tweaked values in `member.extra`
   (`rar.tweaked_crc32` / `rar.tweaked_blake2sp`). Removes the false `CorruptionError`.
2. **Full fix:** when a password is confirmed at open time, verify by forward-transform
   (CRC32 + BLAKE2sp) with the HashKey; leave `member.hashes` empty when the password is
   unknown. Strictly better than DEV (which did CRC only).

## Q2 — 7z folders with no integrity anchor (F2) — **best-effort accept confirmed correct**

Source-confirmed: 7zAES has **no password check** of its own (unlike ZipAES/Rar5) — AES-CBC
just filters, no padding/MAC gate. 7-Zip's only wrong-password gate is the extraction CRC,
and *only when the CRC is defined* (`7zExtract.cpp:95,128`). With no CRC:
- compressed member → the LZMA/LZMA2 decoder usually rejects garbage → `kDataError`;
- **store/copy member → silently returns garbage (`kOK`)**.

So archivey's best-effort accept **exactly matches 7-Zip** — "matching 7-Zip on CRC-less
streams means accepting when decode succeeds, not inventing a password check 7-Zip doesn't
have." Decision stands: keep accept-and-return, emit a `DIGEST_UNVERIFIABLE`/
`DECRYPTION_UNVERIFIED` diagnostic on a no-anchor encrypted member. **F2 stays Low.** (ZIP is
not exposed the same way — AE-2 has a mandatory HMAC, ZipCrypto STORED has the CD CRC.)

## Q3 — 7z KDF cap + `0x3F` sentinel (F3) — **cap at 24 (matches 7-Zip exactly); 0x3F confirmed**

Source-confirmed in `7zAes.cpp`:
- **7-Zip *does* clamp** at property-parse time: `SetDecoderProperties2` accepts
  `NumCyclesPower <= 24` **or** `== 0x3F`, else returns `E_NOTIMPL` (unsupported)
  (`7zAes.cpp:27` `k_NumCyclesPower_Supported_MAX = 24`, `:260-279`). Values 25–62 never reach
  the hash loop, so official 7-Zip never attempts 2⁶³ rounds. **v2 is currently *more*
  permissive than 7-Zip** (accepts 25–62 → the DoS).
- **`0x3F` special case is real** in official 7-Zip: `Key = salt‖password` zero-padded to 32,
  no SHA-256 (`7zAes.cpp:41-50`). archivey/py7zr match — **the "is 0x3F really a sentinel?"
  sub-question is resolved: yes, no divergence.**
- **Counter layout confirmed:** 8-byte little-endian round index appended to `salt‖password`
  (`7zAes.cpp:56-67`). archivey's `(s+i).to_bytes(8,"little")` matches.
- Encoder default is **19**, hardcoded, never user-settable (`7zAes.cpp:232`).

**Plan:** match 7-Zip exactly — in `derive_sevenzip_aes_key` /
`parse_sevenzip_aes_properties`, accept `cycles <= 24` or `cycles == 0x3F`, reject 25–62 with
`UnsupportedFeatureError`. Not merely "our own defensive cap" — it is *the same cap 7-Zip
enforces*, so it rejects nothing a real archive contains.

## Q4 — 7-Zip / UnRAR source checklist — **answered** (`7z-source-questions.md`)

All items A–D answered from source. The only decision-changing surprise is D9 → see F4 below.

## F4 (was "unavoidable") — **unrar password IS avoidable via bare `-p` + stdin**

Source-confirmed correction: unrar supports a non-argv password channel — pass **bare `-p`**
(no value) and write the password to the child's **stdin** (`GetPasswordText → getwstr` reads
stdin when it is redirected; `printf '%s\n' "$pw" | unrar x -p archive.rar`). v2 uses
`unrar p` with the *data* on stdout, so stdin is free — this is directly usable and keeps the
password out of `argv`/`/proc/<pid>/cmdline`. There is still no env-var or `-p@file` channel.

**Plan (F4 upgraded from "documented limitation" to actionable):** change `_password_arg` /
`open_unrar_p` to pass bare `-p` and feed `password + "\n"` via `stdin=PIPE` (keep `-p-` for
the no-password case). Low severity, but now a concrete fix rather than an accepted leak.
