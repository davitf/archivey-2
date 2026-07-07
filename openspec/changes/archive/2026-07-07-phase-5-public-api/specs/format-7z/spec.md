# format-7z — Phase 5 deltas

## MODIFIED Requirements

### Requirement: Decrypt AES-encrypted 7z, honoring per-member passwords

The system SHALL read AES-256-encrypted 7z archives — including header-encrypted
archives — when a password and the `[crypto]` extra are available. Decryption uses
the wrapped crypto backend as a per-folder AES decrypt stage (see
`compressed-streams`); the key is derived per the 7z SHA-256 scheme and applied
ahead of decompression. For a header-encrypted archive the encrypted end header is
decrypted before parsing, so even *listing* requires the password and crypto
backend; without them the system SHALL raise `EncryptionError` (or
`PackageNotInstalledError` if the crypto backend is missing). When the archive (or
any folder) is encrypted, `ArchiveInfo.is_encrypted` SHALL be `True`.

The system SHALL resolve each folder's password through the **candidate model** of
`archive-reading` (known-good list, then remaining sequence candidates, then the
provider callable — the provider receiving the member being decrypted, or `None` for
the header), so members in folders encrypted with different passwords can each be
decrypted within the same open — including in a single forward `stream_members()`
pass — with no global password state and no per-call password parameter. Because 7z
has **no password check value**, trying a candidate costs a full key derivation
(2^19 SHA-256 rounds) plus decoding until corruption surfaces; the reader SHALL cache
derived keys by (password, salt, cycles) and try known-good passwords first, and an
incorrect password SHALL surface as an encrypted/corrupted failure
(`EncryptionError`/`CorruptionError`), not silent garbage.

#### Scenario: members with different passwords

- **WHEN** an encrypted 7z archive holds members in folders encrypted with different passwords and is opened with `password=[pw_a, pw_b]` (or a provider that supplies each on request)
- **THEN** each member decrypts with its matching candidate and verifies its CRC, including during a single streaming pass

#### Scenario: wrong password

- **WHEN** an encrypted member is opened and no candidate decrypts it (or the sole password is incorrect)
- **THEN** decryption fails and surfaces as `EncryptionError`/`CorruptionError`, never incorrect bytes

#### Scenario: header-encrypted 7z opened without a password

- **WHEN** a header-encrypted 7z archive is opened without a password (and any provider returns `None` for the header request)
- **THEN** the system raises `EncryptionError`, because the end header cannot be decrypted to list members
