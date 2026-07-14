# zip-aes-decryption — read WinZip AES-encrypted ZIPs

**Status:** Ready to implement. Depends on zip-native-codec-streams (needs its raw-member-data path). Not breaking (adds a capability). Effort: medium to large.

**Why it matters:** archivey cannot read WinZip AES-encrypted ZIPs at all today — the backend only handles traditional ZipCrypto through the standard library, and the standard library has no AES support, so an AES member simply fails. AES is the default encryption that 7-Zip and WinZip produce, so "the default library for reading ZIPs" silently failing on the common encrypted variant is a sharp corner. Encrypted reading is also squarely the consistency-and-safety flagship of this release. The crypto machinery already exists — it is wired for 7z and RAR — so this is composition, not new infrastructure.

**What it does:** detects the WinZip AES extra field, derives keys from the password with PBKDF2, decrypts with AES in counter mode, authenticates the ciphertext with an HMAC, and feeds the decrypted bytes into the codec layer for the real underlying method.

**Decided:** a wrong password fails fast on the two-byte verification value; a tampered ciphertext fails on the HMAC at end of read. The two AES variants differ on checksums — the newer one, AE-2, stores no CRC and relies on the HMAC, so no crc32 is surfaced and no CRC check runs, while AE-1 keeps and verifies the CRC as well. AES needs the crypto extra; without it an AES member reports a clean "package not installed" error but is still correctly identified as encrypted. Traditional ZipCrypto is untouched.

**Your call later:** confirm the fixture tool emits both AE-1 and AE-2, and that the multi-candidate password flow reuses the AES verification value cleanly.

**Bottom line:** closes a real "can't read this common encrypted ZIP" gap; do it right after the ZIP codec-streams change.
