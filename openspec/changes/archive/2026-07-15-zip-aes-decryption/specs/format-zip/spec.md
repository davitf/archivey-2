## ADDED Requirements

### Requirement: Read WinZip AES-encrypted members

The ZIP backend SHALL read WinZip AES (AE-x) encrypted members: compression method 99 with
the AES extra field `0x9901` giving vendor version (AE-1/AE-2), key strength
(128/192/256), and the actual underlying compression method. Decryption SHALL derive keys
via PBKDF2-HMAC-SHA1 (1000 iterations) over the password and per-member salt
(strength/16 bytes) into encryption key ‖ authentication key ‖ 2-byte verification value,
decrypt with AES-CTR (little-endian counter), and authenticate the ciphertext with
HMAC-SHA1 truncated to 10 bytes. Decrypted bytes SHALL be decompressed through the shared
codec layer for the actual method.

A wrong password SHALL fail fast on the 2-byte verification value with `EncryptionError`
(no bytes returned). A ciphertext HMAC mismatch SHALL raise at the terminal read
(`CorruptionError`). AE-2 members SHALL surface no `crc32` (the ZIP CRC is 0; integrity is
the HMAC) and run no CRC check; AE-1 members SHALL surface and verify `crc32` in addition
to the HMAC. AES decryption requires `[crypto]`; when it is absent an AE member SHALL raise
`PackageNotInstalledError` (detection still identifies the member as AES-encrypted).
Traditional ZipCrypto behavior is unchanged.

#### Scenario: WinZip AES matrix

| Case | Expected |
| --- | --- |
| AE-1 or AE-2 member, 128/192/256, correct password, `[crypto]` present | Decrypts, decompresses via codec layer, HMAC verified at EOF |
| Wrong password | `EncryptionError` on the 2-byte verification value; no bytes |
| Tampered ciphertext, correct password | HMAC mismatch → `CorruptionError` at terminal read |
| AE-2 member | `crc32` absent; no CRC check; HMAC is the integrity signal |
| AE-1 member | `crc32` present and verified alongside the HMAC |
| AES member without `[crypto]` installed | `PackageNotInstalledError`; still reported as encrypted |
| Traditional ZipCrypto member | Unchanged (existing weak-check confirmation path) |
