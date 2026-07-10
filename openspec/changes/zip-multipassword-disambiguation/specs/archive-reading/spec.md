# archive-reading — candidate-password confirmation delta

## ADDED Requirements

### Requirement: Confirming a candidate password before acceptance

A candidate password SHALL be accepted for an encrypted unit — used to serve the unit's
data and added to the per-archive known-good list — only after the format's **authoritative
integrity check** for that unit confirms it. A format's cheap *per-open* password check
(for example ZipCrypto's single verification byte) MAY gate which candidates are worth
trying, but SHALL NOT by itself constitute acceptance when the reader is disambiguating
among multiple candidates, because a weak per-open check admits wrong passwords with
non-negligible probability.

When more than one candidate is available for a unit and the format's per-open check is
weaker than its authoritative check, the reader SHALL disambiguate: it SHALL confirm a
candidate against the authoritative check (for ZIP, decoding to the CRC — see `format-zip`)
before accepting it, and SHALL treat a candidate that passes the per-open check but fails
the authoritative check as a **wrong password**, continuing to the next candidate rather
than reporting corruption. A failure that is not attributable to a wrong password (a
genuinely corrupt or truncated unit) SHALL still surface as the appropriate read error.

When exactly one candidate remains after the per-open filter, the reader MAY accept it
without the extra authoritative pass (there is nothing to disambiguate). When multiple
candidates satisfy **every** available check for a unit (a genuine ambiguity), the reader
SHALL NOT silently return data decrypted with an unconfirmed password: it SHALL either
raise `EncryptionError` for that unit, or select a candidate deterministically **and record
that the selection was unconfirmed** (surfaced as data where a warnings/occurrences
mechanism exists, otherwise logged). Backends whose key derivation already authenticates
the password strongly (e.g. 7z AES, RAR5) satisfy this requirement with their existing
check and are unaffected.

#### Scenario: a wrong candidate that passes a weak per-open check does not shadow the right one

- **WHEN** an encrypted member is opened with two candidate passwords and the wrong one, tried first, happens to pass the format's weak per-open check
- **THEN** the reader confirms candidates against the authoritative integrity check, rejects the wrong one, and reads the member with the correct password — it does not report a spurious corruption error

#### Scenario: only wrong passwords are supplied

- **WHEN** none of the supplied candidates is correct for an encrypted member (some may pass the weak per-open check)
- **THEN** the reader raises `EncryptionError` for that member, never silently returning data decrypted with an unconfirmed password

#### Scenario: a genuinely corrupt encrypted member is not mislabelled

- **WHEN** the correct password is supplied for an encrypted member whose stored data is corrupt
- **THEN** the failure surfaces as the appropriate read error (corruption/truncation), not as a wrong-password result
