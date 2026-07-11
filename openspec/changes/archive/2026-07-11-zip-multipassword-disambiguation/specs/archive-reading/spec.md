# archive-reading — weak password-check confirmation delta

## ADDED Requirements

### Requirement: Confirm candidates when a weak check permits retries

When an implemented format has a password check that can admit wrong values, a candidate
SHALL NOT be accepted or added to the per-archive known-good list on that weak check alone
when another distinct candidate may be tried. The backend SHALL first confirm the
candidate with a stronger check — the strongest available signal that can reject a wrong
candidate within bounded work (a bounded decompression prefix, a per-candidate checksum
computed in one shared pass, or full validation when the unit is small). Confirmation
SHALL obey the "Bounded implicit temporary storage" requirement: it SHALL NOT buffer
plaintext proportional to the unit size to memory or temporary storage.

After confirmation, the backend MAY re-open or re-decode the accepted candidate to
produce the caller's stream (formats whose sources are seekable always can). The returned
stream SHALL retain the format's ordinary read-time integrity checking, so
bounded confirmation never weakens the read-time contract relative to the
single-candidate path: data that is wrong beyond what confirmation examined still fails
on the caller's read exactly as it would have with a single password.

“Another candidate may be tried” includes two or more distinct known-good/static values
and a provider that can return another answer after failure. A provider SHALL remain lazy;
the reader SHALL NOT enumerate it in advance or assume it is finite. Duplicate values do
not create another distinct candidate. An `EncryptionError` raised by the provider
callback itself is a provider failure, not a candidate decrypt result; it SHALL propagate
without being rewritten as candidate exhaustion or password/corruption ambiguity.

If confirmation fails after a weak check and all candidates are exhausted, the result can
be intrinsically ambiguous: the candidate may be wrong, or it may be correct and the
encrypted unit corrupt. The reader SHALL describe both possibilities rather than promise
an impossible classification. It MAY use `EncryptionError` for this candidate-exhaustion
state. It SHALL NOT return an unvalidated candidate based on order, heuristics, or a
warning.

A single distinct static candidate MAY retain the format's normal lazy streaming path;
read-time integrity failures on that path retain the format's ordinary corruption/error
translation. This requirement does not assign check strength or authentication behavior
to formats whose readers are not implemented.

#### Scenario: a wrong candidate that passes a weak per-open check does not shadow the right one

- **WHEN** an encrypted member is opened with two candidate passwords and the wrong one, tried first, happens to pass the format's weak per-open check
- **THEN** the reader rejects the wrong candidate through confirmation and returns a stream opened with the correct candidate

#### Scenario: confirmation is bounded

- **WHEN** an encrypted member far larger than the confirmation bound is opened with multiple candidates
- **THEN** candidate confirmation completes without buffering plaintext proportional to the member size to memory or temporary storage

#### Scenario: provider remains lazy but retryable

- **WHEN** a provider's first answer passes a weak check but fails confirmation
- **THEN** the reader requests the provider's next answer without pre-enumerating it, and accepts an answer only after confirmation

#### Scenario: provider failure is not candidate exhaustion

- **WHEN** a candidate fails confirmation and the provider callback subsequently raises its own `EncryptionError`
- **THEN** that provider exception propagates unchanged rather than being replaced by the candidate-exhaustion ambiguity error

#### Scenario: exhausted confirmation reports the irreducible ambiguity

- **WHEN** one or more candidates pass a weak check but fail confirmation and no candidate succeeds
- **THEN** the failure states that the passwords may be wrong or the encrypted unit may be corrupt, and no candidate's bytes are returned

#### Scenario: one distinct static candidate retains lazy streaming

- **WHEN** the password input contains one distinct static value, including duplicate copies of that value
- **THEN** the member is not eagerly consumed solely for candidate disambiguation, and ordinary read-time error translation applies

### Requirement: Bounded implicit temporary storage

Reader operations SHALL NOT consume memory or temporary storage proportional to a
member's or the archive's size as an implicit side effect of opening, reading, validating,
or password-confirming a member. Silently spooling member plaintext to a temporary file —
however bounded in RAM — is such a side effect and is not permitted: a caller who opens a
member has consented to streaming reads, not to a hidden on-disk copy of the member.

A per-format materialization strategy that inherently requires proportional temporary
storage (for example `format-rar`'s documented `unrar x`-to-temporary-directory serving
strategy) is permitted only when it is explicitly declared in that format's capability
spec; such strategies are format-level documented behavior, not implicit side effects.
This requirement does not restrict the caller's own buffering of a returned stream.

#### Scenario: opening an encrypted member with many candidates stays bounded

- **WHEN** an encrypted member of arbitrary size is opened with multiple password candidates
- **THEN** candidate confirmation uses temporary memory/storage bounded by a constant, not by the member size

#### Scenario: proportional strategies must be declared per format

- **WHEN** a backend can only serve member data by materializing it (e.g. an external-binary extraction strategy)
- **THEN** that strategy is declared in the format's capability spec rather than adopted silently by a reader operation
