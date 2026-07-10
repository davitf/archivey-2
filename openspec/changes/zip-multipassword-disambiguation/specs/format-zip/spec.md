# format-zip — ZipCrypto multi-candidate disambiguation delta

## ADDED Requirements

### Requirement: Multi-candidate disambiguation for traditional ZipCrypto

For a member encrypted with traditional ZipCrypto (PKWARE), whose per-open password check
is a single verification byte while the authoritative check is the member's CRC-32 (and,
for a compressed member, the decompressor), the ZIP reader SHALL confirm a candidate
password by the authoritative check before accepting it whenever it is disambiguating among
multiple candidates (per `archive-reading` → "Confirming a candidate password before
acceptance"). It SHALL do so with the following ordered ladder, stopping as soon as one
candidate remains:

1. **Per-open filter.** Discard candidates whose verification byte does not match (a
   mismatching candidate is a wrong password). If exactly one candidate remains, it MAY be
   accepted without further decoding.
2. **Compressed-member decode probe.** For a compressed member, decode an initial block
   under each remaining candidate; a decompression error discards that candidate.
3. **Size-gated full verification.** For a member whose uncompressed size is within a
   configured budget, decode it fully and check the CRC under each remaining candidate;
   discard CRC failures. Above the budget the reader SHALL NOT fully decode every candidate.
4. **Residual selection.** If more than one candidate still remains, the reader MAY prefer a
   candidate by neighbour-member affinity (the password that decrypted an adjacent member)
   or content plausibility, and SHALL resolve the residual per the `archive-reading`
   requirement (raise `EncryptionError`, or select deterministically and record that the
   selection was unconfirmed).

The single-candidate path SHALL retain its current behavior (no eager full read). A member
whose data fails the authoritative check under the confirmed/only password SHALL be
reported as corruption, not as a password error.

#### Scenario: wrong candidate eliminated by the compressed-member decode probe

- **WHEN** a DEFLATE-compressed encrypted member is opened with a wrong candidate (tried first, passing the verification byte) and the correct candidate
- **THEN** the wrong candidate is eliminated by a decompression error on the initial block and the member is read with the correct candidate

#### Scenario: single candidate keeps the fast path

- **WHEN** a ZipCrypto member is opened with exactly one candidate password
- **THEN** the reader does not perform an eager full-member read to confirm it; the member streams as before

#### Scenario: large member exceeds the decode budget

- **WHEN** two candidates both pass the verification byte for a stored member larger than the verification budget
- **THEN** the reader does not fully decode the member under every candidate; it resolves the residual per the cross-format ambiguity rule rather than reading unboundedly
