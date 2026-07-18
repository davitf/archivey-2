# Brief 2 — Native decryption & verification: crypto correctness deep review

Read `review/next/README.md` first. This brief owns **all native cryptographic
code**: key derivation, decryption, and integrity verification across every format.
Reviewed as one pass because it cross-cuts formats and is the highest
consequence-per-bug surface in the tree — a crypto bug does not crash, it silently
accepts tampered or wrong data, which directly attacks VISION claim #3 ("an honest
error" on damaged input) and the project's whole trust proposition.

## Why this review, now

At the old baseline there was essentially no native crypto to review. Since #73 the
tree grew ~5 native primitives, **none previously reviewed**:

- **WinZip AES** (#106, `internal/zip_aes.py`) — PBKDF2 key derivation, AES-CTR-LE
  (`_AesCtrLe`, hand-rolled counter), HMAC-SHA1 authentication, password
  verification bytes.
- **7z AES + KDF** (`internal/streams/crypto.py`) — `derive_sevenzip_aes_key` (the
  SHA-256 cycle KDF), `AesDecryptStream`, `SevenZipKeyCache`,
  `parse_sevenzip_aes_properties`.
- **ZipCrypto** (`internal/zipcrypto.py`) — legacy PKWARE stream cipher.
- **RAR header/data key derivation & decrypt** (`rar_parser.py`: `_rar3_s2k`,
  `_rar5_s2k`, `_Rar3Sha1` incl. the deliberate `rarbug` corruption path,
  `_HeaderDecryptStream`, `_rar{3,5}_decrypt_header`).
- **Native BLAKE2sp** (#104, `internal/hashing/blake2sp.py`) — from-scratch parallel
  BLAKE2 for RAR5 checksum verification (stdlib has blake2b/blake2s, not blake2sp).

All AES paths require the `cryptography` extra (`[crypto]`); the KDF/verification
glue and BLAKE2sp and ZipCrypto are pure-Python. `py7zr`/`rarfile` are oracles only.

## Boundary with Brief 1 (RAR)
Brief 1 owns the *structural/hostile-input* safety of RAR's crypto (malformed
encrypted headers, truncated salt/IV, bounds). This brief owns the *cryptographic
correctness* of the same RAR primitives. Coordinate on the seam.

## What to hunt (ranked)

### A. Silent-acceptance / verification bugs (top priority)
The worst outcome is "wrong password or tampered data decrypts to garbage and is
returned without error." For every path:
- **Authentication actually happens and gates output.** WinZip AES: is the HMAC-SHA1
  over the ciphertext checked, and is a mismatch raised as `EncryptionError`/
  `CorruptionError` *before* bytes are handed to the caller — or can truncated/last-
  block data escape unverified? Is the 2-byte password-verification value checked,
  and is passing it (but failing HMAC) distinguished correctly?
- **7z:** folder/member CRC is the wrong-password oracle — confirm a wrong key is
  rejected by CRC on *every* solid/non-solid path, and that an archive with CRC
  absent doesn't silently accept any password.
- **RAR5:** BLAKE2sp checksum verification — is it wired to gate member data, and is
  the native BLAKE2sp bit-exact vs a reference (test against known vectors and the
  `rarfile`/`unrar` oracle across message sizes that cross the parallel-lane and
  block boundaries — the classic blake2sp bug locations)?
- **Ordering:** decrypt-then-verify vs verify-then-decrypt — is integrity checked on
  ciphertext or plaintext per each format's spec, and does the code match?

### B. KDF & cipher correctness
- `derive_sevenzip_aes_key`: SHA-256 cycle count (`2^cycles`), salt+password+counter
  layout, endianness of the counter — bit-exact vs the 7z spec / py7zr oracle,
  including `cycles == 0x3F` (infinite) rejection and the empty-salt case.
- `derive_winzip_aes_keys`: PBKDF2-HMAC-SHA1 iteration count (1000), the
  key1||key2||verify split lengths per AE strength (128/192/256), salt length per
  strength.
- `_rar3_s2k` / `_rar5_s2k`: iteration counts, salt/IV derivation, UTF-16LE vs UTF-8
  password normalization (`_normalize_password_utf16le` vs `_normalize_password_utf8`
  — RAR3 and RAR5 differ; a mismatch means valid passwords silently fail).
- `_AesCtrLe`: the hand-rolled little-endian CTR counter — does it increment
  correctly across the 32-bit/128-bit boundary, and is the block offset on a seek/
  re-open recomputed (or is CTR state assumed sequential in a seekable member)?
- ZipCrypto: key-update loop and the CRC/time-based header-byte check.

### C. Streaming, seek, and lifecycle
The decrypt stages are `ReadOnlyIOStream`s inside the seekable/streaming machinery.
- Does seeking within an encrypted member recompute cipher state correctly (CTR
  offset, CBC IV chaining) or silently return wrong plaintext after a seek?
- Partial/truncated reads: does a short final block raise, or emit unauthenticated
  bytes? (VISION #3 wants a member-level honest error, not garbage.)
- `SevenZipKeyCache` / RAR per-folder key cache: is a cached key ever reused across a
  salt/IV it wasn't derived for? Thread-safety of the cache under CONCURRENT?

### D. Availability & error contract
- `[crypto]` absent: is the failure a clean typed `UnsupportedFeatureError`/
  `PackageNotInstalledError` at the right point (not an `ImportError` escaping, not a
  misleading `CorruptionError`)? Reproduce in the `[core-only]` config.
- Does any decrypt path leak the password into an exception message, log, repr, or
  `unrar` process argv?
- Minimum-version leg: `cryptography>=45` is the floor — any API used that isn't in
  45.0? (Check in the `[all-lowest]` config.)

### E. Constant-time / side-channel (scope-appropriate)
Note, don't over-index: are password-verification and HMAC comparisons done with a
constant-time compare (`hmac.compare_digest`) rather than `==`? This is a real but
low-severity finding for a local archive library; flag it as hardening, not a
release blocker, unless a specific path compares secrets with `==`.

## Non-goals / already settled
- Don't propose replacing `cryptography` with a pure-Python AES, or adding new cipher
  support. Don't re-derive the "AES needs an extra" decision (`packaging-and-extras`).
- `py7zr`/`rarfile` as *oracles* is intended.
- BCJ2 rejection is intended (not a crypto issue).

## Deliverable
Per README. Suggested theme files: `verification.md` (the silent-acceptance
analysis — the headline), `kdf-and-ciphers.md`, `availability-and-contract.md`.
Back every correctness claim with a **test vector or oracle diff**, not prose —
name the message size / block boundary / cycle count that triggers it, and the
dependency config. A crypto finding without a reproducing input is a hypothesis, not
a finding; label it as such.
