## ADDED Requirements

### Requirement: WinZip AES ZIP corpus and failure cases

The corpus SHALL include WinZip AES ZIP entries covering AE-1 and AE-2, key strengths
128 and 256, over STORED and DEFLATE members, cross-validated against an oracle (`7z`/
`py7zr`) for decrypted bytes; oracle-backed cases SHALL skip when the tool/library is
unavailable. Dedicated tests SHALL assert wrong-password (`EncryptionError`, no bytes),
tampered-HMAC (`CorruptionError`), AE-2 absent-`crc32`, and missing-`[crypto]`
(`PackageNotInstalledError`, still reported encrypted).

#### Scenario: AES ZIP coverage

| Case | Expected |
| --- | --- |
| AE-1/AE-2 × 128/256 × STORED/DEFLATE, correct password | Bytes match the oracle; skip if oracle absent |
| Wrong password | `EncryptionError`, no bytes |
| Tampered ciphertext | `CorruptionError` at terminal read |
| AE-2 member | No `crc32` surfaced (parity sweep expects its absence) |
| `[crypto]` not installed | `PackageNotInstalledError`; member still identified as encrypted |
