# archive-reading — weak password-check confirmation delta

## ADDED Requirements

### Requirement: Confirm candidates when a weak check permits retries

When an implemented format has a password check that can admit wrong values, a candidate
SHALL NOT be accepted or added to the per-archive known-good list on that weak check alone
when another distinct candidate may be tried. The backend SHALL first run the strongest
available full-unit integrity validation. It SHALL return only bytes from the validation
pass that succeeded, or an equivalent stream over those retained validated bytes; it
SHALL NOT decrypt the winner a second time merely to restart the caller's stream.

“Another candidate may be tried” includes two or more distinct known-good/static values
and a provider that can return another answer after failure. A provider SHALL remain lazy;
the reader SHALL NOT enumerate it in advance or assume it is finite. Duplicate values do
not create another distinct candidate. An `EncryptionError` raised by the provider
callback itself is a provider failure, not a candidate decrypt result; it SHALL propagate
without being rewritten as candidate exhaustion or password/corruption ambiguity.

If validation fails after a weak check and all candidates are exhausted, the result can be
intrinsically ambiguous: the candidate may be wrong, or it may be correct and the
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
- **THEN** the reader rejects the wrong candidate through full-unit validation and returns the validated bytes produced with the correct candidate

#### Scenario: provider remains lazy but retryable

- **WHEN** a provider's first answer passes a weak check but fails full validation
- **THEN** the reader requests the provider's next answer without pre-enumerating it, and accepts an answer only after full validation

#### Scenario: provider failure is not candidate exhaustion

- **WHEN** a candidate fails validation and the provider callback subsequently raises its own `EncryptionError`
- **THEN** that provider exception propagates unchanged rather than being replaced by the candidate-exhaustion ambiguity error

#### Scenario: exhausted validation reports the irreducible ambiguity

- **WHEN** one or more candidates pass a weak check but fail full validation and no candidate succeeds
- **THEN** the failure states that the passwords may be wrong or the encrypted unit may be corrupt, and no candidate's bytes are returned

#### Scenario: one distinct static candidate retains lazy streaming

- **WHEN** the password input contains one distinct static value, including duplicate copies of that value
- **THEN** the member is not eagerly consumed solely for candidate disambiguation, and ordinary read-time error translation applies
