# Maintainer decisions — Brief 2 (crypto)

These are the calls I did not want to make silently (CLAUDE.md: pause and ask on
spec/behaviour discrepancies rather than resolving them).

## Q1 — RAR5 tweaked-checksum BLAKE2sp (F1)

When a RAR5 member is encrypted with tweaked checksums (`RAR5_XENC_TWEAKED`), the stored
BLAKE2sp is key-tweaked and cannot be checked against the plaintext hash without reproducing
the inverse tweak. Two options:

- **(recommended) Skip it**, matching rarfile and matching what `_member_hashes` already does
  for crc32 — drop blake2sp from `member.hashes` when tweaked, optionally emitting a
  `DIGEST_UNVERIFIABLE` diagnostic. Minimal, restores correct reads immediately.
- **Implement the inverse tweak** so archivey actually verifies tweaked BLAKE2sp (and could
  re-enable the tweaked crc32 too). More faithful to WinRAR, but adds crypto surface and
  needs a `-htb`-encrypted fixture to test.

Either way the current behaviour (raise `CorruptionError` on good data) is wrong. Which
direction?

## Q2 — 7z folders with no integrity anchor (F2)

An encrypted 7z folder with no folder digest and CRC-less members currently confirms *any*
password and returns garbage. The safe fix is to **fail closed** — refuse to confirm a
password when there is no checksum to validate against. Does that conflict with any planned
"recover what you can from a damaged/odd archive" behaviour? My read of VISION #3 is that a
*wrong-password-not-detected* is strictly worse than a refusal, so fail-closed is right — but
confirm you want an encrypted-but-checksum-less member to hard-error rather than hand back
unverified bytes.

## Q3 — 7z KDF cap and the `0x3F` sentinel (F3)

1. **Cap `NumCyclesPower`?** RAR5 caps its KDF shift at 24; 7z is uncapped below the `0x3F`
   sentinel, so `0x3E` = ~2⁶² rounds. I recommend capping at 24 (raise
   `UnsupportedFeatureError` above it). Agree on 24, or a different ceiling?
2. **Is `0x3F` really a "no-hash" sentinel?** archivey follows py7zr (copy `salt+password`,
   no hashing). If the 7-Zip C++ reference instead treats `0x3F` as `2^63` iterations, both
   archivey and py7zr are wrong for that one value — but no creator emits `0x3F`, so it is a
   crafted-only concern. Worth a one-line confirmation against the reference, or explicitly
   accept "bit-exact with py7zr (our oracle)" as the contract and move on?
