# Source-verification checklist (for the 7-Zip / UnRAR source agent)

> **STATUS: ANSWERED (2026-07-16).** All items below were answered from the 7-Zip and UnRAR
> source; the confirmed answers are summarised in the **"Answers"** section at the bottom and
> folded into `QUESTIONS.md` (final decisions) and the theme files. Kept here as the audit
> trail.

Questions this crypto review could not settle from Python oracles alone. Each is phrased so a
source-reading agent can answer it with a file/line citation. Two codebases:

- **7-Zip** (`p7zip` / 7-Zip source): `C/Sha256.c`, `CPP/7zip/Crypto/7zAes.{h,cpp}`,
  `CPP/7zip/Crypto/MyAes.cpp`.
- **UnRAR** (RARLAB `unrar` source): `crypt.cpp`, `crypt5.cpp`, `hash.cpp`, `blake2sp.cpp`,
  `headers.hpp`, `archive.cpp`.

## A. 7z AES key-derivation (`NumCyclesPower`) — for F3

1. In `7zAes.cpp` (`CKeyInfo::CalcKey` / the SHA-256 loop): does the decoder **bound**
   `NumCyclesPower` before looping `1 << NumCyclesPower` times, or does it loop unbounded on
   whatever the archive stored? Cite the exact loop and any clamp.
2. Is there a `0x3F` (63) **special case** ("no hashing: key = salt‖password padded to 32")?
   py7zr and archivey both implement one. Does official 7-Zip? If **not**, then for a crafted
   `NumCyclesPower == 0x3F` archivey diverges from 7-Zip (7-Zip would attempt 2⁶³ rounds).
   Quote the branch (or confirm its absence).
3. What is the encoder's default `NumCyclesPower`? (Expected: 19.) Any code path that lets a
   user raise it, and to what maximum?
4. Confirm the counter fed into SHA-256 is the 64-bit little-endian round index appended to
   `salt‖password` each round (archivey/py7zr do this) — cite the byte layout.

## B. 7z wrong-password detection without CRC — for F2

5. When an encrypted 7z folder/substream has **no defined CRC** (no `UnpackDigests`,
   CRC-less members), how does 7-Zip decide the password was wrong? Does it (a) rely solely on
   CRC and therefore silently return garbage when CRC is absent, or (b) have another gate
   (padding check, coder-level validation)? Cite the extraction path in
   `7zHandler`/`7zDecode`. This decides whether archivey's best-effort accept matches 7-Zip.

## C. RAR5 tweaked-checksum transform (`ConvertHashToMAC`) — for F1

6. In `hash.cpp` `ConvertHashToMAC` (or wherever the tweak is applied), confirm the exact
   transform for **both** hash types:
   - CRC32: `HMAC-SHA256(HashKey, RawPut4(crc))` → XOR the eight 32-bit words → new CRC32.
     (archivey-dev implements exactly this — confirm bit layout: little-endian `RawPut4`,
     little-endian `RawGet4` on the digest words.)
   - **BLAKE2sp**: confirm it is `HMAC-SHA256(HashKey, blake2sp_digest[32])` copied over the
     32-byte digest (my reading). This is the piece archivey-dev never implemented; v2 needs
     it to verify tweaked `-htb` members.
7. Confirm the **HashKey** used by `ConvertHashToMAC` is the PBKDF2-HMAC-SHA256 output at
   `(1 << Kdf_Count) + 16` iterations (16 iterations past the AES key; PswCheck is at +32).
   Cite `crypt5.cpp` where Key / HashKey / PswCheck are split out of the single derivation.
8. Confirm the tweak is applied **only** when the file's encryption record has the
   `RAR5_XENC_TWEAKED` (0x02) flag, and that header-encrypted archives (encrypted file names)
   store **un-tweaked** checksums (the WinRAR docs say so; confirm in source).

## D. unrar password channel — for F4

9. Does RARLAB `unrar` support any **non-interactive** password channel other than
   `-p<password>` on the command line — e.g. reading from stdin, a file, or an environment
   variable — that avoids exposing the password in `argv`/`/proc`? Check `CmdExtract` /
   `GetPassword` / option parsing. (If not, `-p<pwd>` is unavoidable and F4 is a documented
   limitation.)

---

# Answers (source-confirmed, 2026-07-16)

## A — 7z AES key-derivation
- **A1 (bound?)** Yes, but at *property-parse* time, not in the hash loop. `CalcKey` loops
  `1 << NumCyclesPower` unbounded, but `SetDecoderProperties2` accepts `NumCyclesPower <= 24`
  (`7zAes.cpp:27` `k_NumCyclesPower_Supported_MAX = 24`) **or** `== 0x3F`, else `E_NOTIMPL`
  (`7zAes.cpp:260-279`). Values 25–62 never reach `CalcKey`. → **v2 is currently more permissive
  than 7-Zip**; match 7-Zip (accept ≤24 or ==0x3F).
- **A2 (0x3F sentinel?)** Yes, official 7-Zip has it: `Key = salt‖password` zero-padded to 32,
  no SHA-256 (`7zAes.cpp:41-50`). archivey/py7zr match — no divergence.
- **A3 (encoder default/max)** Hardcoded 19; the `0x3F` line is commented out; `CEncoder` has no
  `ICompressSetCoderProperties`, so no UI/CLI can change it (`7zAes.cpp:232`). Always writes 19.
- **A4 (counter layout)** 8-byte little-endian round index appended to `salt‖password`; per round
  `SetUi32` writes the low 32 bits LE, high 4 bytes stay 0 (`7zAes.cpp:56-67`). Matches
  archivey's `(s+i).to_bytes(8,"little")`.

## B — 7z wrong-password detection without CRC
- **B5** 7zAES has **no** password check; AES-CBC just filters. Wrong-password detection is the
  extraction CRC gate, and only when CRC is defined (`7zExtract.cpp:95` `_calcCrc = CheckCrc &&
  fi.CrcDefined && !fi.IsDir`; `:128` returns `kOK` when `!_calcCrc`). No CRC + compressed → LZMA
  usually rejects garbage → `kDataError` (`7zExtract.cpp:397-411`); **no CRC + store/copy →
  silently OK with garbage.** → archivey's best-effort accept matches 7-Zip; do not invent a
  password check.

## C — RAR5 tweaked-checksum transform
- **C6** `ConvertHashToMAC` (`crypt5.cpp:193-211`): CRC32 → XOR-fold of `HMAC-SHA256(HashKey,
  RawPut4(crc))` (LE, matches archivey-dev); BLAKE2sp → `HMAC-SHA256(HashKey, digest[32])`
  overwriting the 32 bytes (matches this review's reading; DEV never implemented it).
- **C7** HashKey = PBKDF2-HMAC-SHA256 at `(1<<Lg2Cnt)+16`; AES key at `1<<Lg2Cnt`, PswCheck at
  `+32` — one chain, three cutpoints (`crypt5.cpp:104,161`). PswCheck folded to 8 bytes by XOR.
- **C8** Tweak applies iff the per-file `FHEXTRA_CRYPT_HASHMAC` (0x02) flag is set
  (`headers5.hpp:103`, `arcread.cpp:1080`, `extract.cpp:934`). Encrypted-header (`-hp`) archives
  do **not** auto-skip in the reader — unrar keys only off 0x02. v2's `_crc_is_tweaked` already
  reads exactly this flag.

## D — unrar password channel
- **D9** No env var, no `-p@file`. But **bare `-p` reads the password from stdin when stdin is
  redirected** (`GetPasswordText → getwstr`): `printf '%s\n' "$pw" | unrar x -p archive.rar`.
  Non-interactive, keeps the secret out of `argv`/`/proc`. v2 uses `unrar p` (data on stdout),
  so stdin is free → directly usable. → F4 is fixable, not just a documented limitation.
