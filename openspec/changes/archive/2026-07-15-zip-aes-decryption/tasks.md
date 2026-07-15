## 1. AE detection + key derivation

- [x] 1.1 Parse the `0x9901` AES extra field (vendor version AE-1/AE-2, strength, actual method); recognize method 99
- [x] 1.2 PBKDF2-HMAC-SHA1 (1000 iters) → enc key ‖ auth key ‖ 2-byte verify; salt length by strength (8/12/16)
- [x] 1.3 Fast-fail wrong password on the verification value (`EncryptionError`, no bytes)

## 2. Decrypt + compose

- [x] 2.1 AES-CTR decrypt stage over the raw ciphertext slice (from the `zip-native-codec-streams` path), via `[crypto]`
- [x] 2.2 HMAC-SHA1(10) authentication of the ciphertext, finalized at EOF → `CorruptionError` on mismatch
- [x] 2.3 Feed decrypted bytes to the codec layer for the actual method (STORED/DEFLATE/…)
- [x] 2.4 `[crypto]` absent → `PackageNotInstalledError` (detection still reports encrypted)

## 3. Hash/CRC semantics

- [x] 3.1 AE-2: surface no `crc32`, run no CRC check (HMAC is integrity); update the stored-digest parity sweep to expect absence
- [x] 3.2 AE-1: surface and verify `crc32` alongside the HMAC

## 4. Fixtures + tests

- [x] 4.1 AES ZIP fixtures (AE-1/AE-2 × 128/256 × STORED/DEFLATE) generated on demand via `7z`/`py7zr`; skip when absent
- [x] 4.2 Wrong-password, tampered-HMAC, AE-2 absent-`crc32`, missing-`[crypto]` cases
- [x] 4.3 Confirm the multi-candidate password flow reuses the AE verify value cleanly

## 5. Verify

- [x] 5.1 Run across `[all]`, `[all-lowest]`, `core-only` (AES path gated on `[crypto]`; core-only asserts the `PackageNotInstalledError` behavior)
- [x] 5.2 `openspec validate --strict zip-aes-decryption`
