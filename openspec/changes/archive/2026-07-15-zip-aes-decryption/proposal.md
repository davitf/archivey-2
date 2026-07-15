## Why

Archivey cannot read **WinZip AES-encrypted** ZIP members at all today — the backend
only handles traditional ZipCrypto (via stdlib `zipfile`), and stdlib `zipfile` has no
AES support, so an AE-x member (compression method 99 + extra field `0x9901`) fails
rather than decrypting. AES is the *default* encryption 7-Zip and WinZip produce, so
"the default library for reading ZIPs" silently failing on the common encrypted variant
is a sharp compatibility corner — and encrypted-read is squarely the consistency + safety
flagship of the first release. The `[crypto]` AES machinery already exists (wired for
7z/RAR), and the raw-member-data path from `zip-native-codec-streams` already yields the
raw member bytes — so this is composition, not new infrastructure. Parked in `IDEAS.md`
tied to the eventual native parser, but it does not need the full parser.

## What Changes

- Detect WinZip AE encryption: compression method **99** with the AES extra field
  **`0x9901`** carrying vendor version (**AE-1**/**AE-2**), key strength (128/192/256),
  and the *actual* compression method used underneath.
- Decrypt natively via `[crypto]`: PBKDF2-HMAC-SHA1 key derivation from the password +
  per-member salt (8/12/16 bytes by strength) into encryption key ‖ authentication key ‖
  2-byte password-verification value; **AES-CTR** (little-endian counter) over the
  ciphertext; **HMAC-SHA1** (truncated to 10 bytes) authentication of the ciphertext.
- Compose with the codec layer: the decrypted bytes are the compressed member body, fed
  to the actual method (STORED/DEFLATE/…) via the `zip-native-codec-streams` path.
- Integrity/hash handling: **AE-2** stores no CRC (CRC field is 0) — integrity is the
  HMAC, so no `crc32` is surfaced and no CRC verification runs; **AE-1** keeps the CRC and
  verifies it in addition to the HMAC. A wrong password fails fast on the verification
  value; a tampered ciphertext fails on the HMAC → `EncryptionError`/`CorruptionError`.
- Traditional ZipCrypto behavior is unchanged (this change is AE-x only).

## Capabilities

### New Capabilities

<!-- none -->

### Modified Capabilities

- `format-zip`: read WinZip AE-1/AE-2 members (method 99 / extra `0x9901`) — AES-CTR +
  PBKDF2 key derivation + HMAC-SHA1 auth via `[crypto]`; AE-2 CRC/hash semantics; missing
  `[crypto]` → `PackageNotInstalledError`.
- `testing-contract`: AES-encrypted ZIP corpus entries (AE-1/AE-2 × 128/256 × STORED/
  DEFLATE) cross-validated against an oracle; wrong-password and tampered-HMAC cases.

## Impact

- `zip_reader.py`: AE extra-field parse; a decrypt stage (`[crypto]` AES-CTR + PBKDF2 +
  HMAC) feeding the raw-data codec path from `zip-native-codec-streams`.
- Public surface: AES ZIPs become readable with a password; AE-2 members expose no
  `crc32`; missing `[crypto]` raises `PackageNotInstalledError`; wrong password →
  `EncryptionError` (no bytes).
- **Depends on `zip-native-codec-streams`** (needs its raw-member-data path). Target
  0.2.0; acceptable to slip to the first fast-follow if release timing is tight, but the
  compat gap should be documented either way.
