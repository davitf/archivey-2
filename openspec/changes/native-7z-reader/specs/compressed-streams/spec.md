## ADDED Requirements

### Requirement: 7z AES key derivation feeds the shared crypto stage

The system SHALL derive 7z AES-256 keys using the 7z SHA-256 scheme (UTF-16LE
password, salt, `1 << NumCyclesPower` rounds — with the documented `0x3f` special
case) and pass the resulting key and IV to the shared crypto backend as `AesParams`.
Because this KDF is 7z-specific (RAR and WinZip-AES derive keys differently), it SHALL
live as a **7z-local helper within the crypto layer**, not on the generic crypto
backend surface — so a format's key-derivation scheme does not accrete onto the shared
AES stage. The shared AES decrypt stage itself stays format-agnostic and consumes only
the derived `AesParams`. Derived keys SHALL be cacheable by `(password, salt, cycles)`
for reuse across folders/header decrypts within one reader. Format parsers MUST NOT
import `cryptography` directly. PPMd var.H streams SHALL open through the shared
`PpmdCodec` once parameters from the 7z coder properties are supplied.

#### Scenario: 7z folder decrypt uses derived AesParams

- **WHEN** an AES-encrypted 7z folder is decoded with a correct password and `[crypto]` installed
- **THEN** the reader derives the key via the 7z KDF, builds an AES-CBC decrypt stage through the crypto wrapper, then decompresses

#### Scenario: derived keys are reused for the same KDF inputs

- **WHEN** two folders share the same `(password, salt, cycles)` within one open reader
- **THEN** the KDF result is reused from the cache rather than recomputed

#### Scenario: PPMd var.H opens via PpmdCodec

- **WHEN** a 7z folder coded as PPMd var.H is decoded and `pyppmd` is installed
- **THEN** decompression goes through the shared PPMd codec with the coder's parameters
