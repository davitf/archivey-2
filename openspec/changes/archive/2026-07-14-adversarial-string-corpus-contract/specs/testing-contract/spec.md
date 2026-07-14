## MODIFIED Requirements

### Requirement: Adversarial corpus coverage

The system SHALL include an adversarial test corpus that exercises every documented attack category and verifies that the correct exception is raised or limit is enforced in each case. The required adversarial cases are:

| Case | Expected outcome |
|---|---|
| Zip bomb — quine-style and nested (42.zip variant) | `max_ratio` and `max_extracted_bytes` limits enforced |
| Ratio-floor false positive — tiny highly-compressible file (10 B → 15 KiB, 1500:1) | Extracts **without** error; output stays under `ratio_activation_threshold` |
| Path traversal — `../evil`, `../../etc/passwd`, `./../../outside` | `PathTraversalError` raised |
| Absolute paths — `/etc/passwd`, `C:\Windows\System32\evil.dll` | `PathTraversalError` raised |
| Symlink escape — symlink pointing to `../../outside`, and chained symlinks | `SymlinkEscapeError` raised |
| Symlink loop — cyclic symlinks (`a → b`, `b → a`) | `SymlinkEscapeError` raised; no uncaught `OSError`/crash |
| Corrupt archive — truncated ZIP (missing EOCD), truncated TAR, bad CRC | `CorruptionError` or `TruncatedError` raised |
| Unicode bombs — `\x00` in paths, RTL override characters in filenames | `PathTraversalError` raised (for null bytes); warning or rejection for RTL |
| Giant claimed size — member claims 1 TiB uncompressed but archive is 1 KiB | Extraction aborts cleanly before exhausting resources |

Regenerable adversarial archives SHALL be generated deterministically in memory or on
demand by `tests/create_adversarial.py` and SHALL NOT be committed. A hostile archive that
cannot be generated in the test environment MAY be committed under
`tests/fixtures/adversarial/` only with the fixture-policy JSON sidecar and an explicit
rationale.

The RTL warning/rejection outcome applies to every `ArchiveMember` presented by any
backend, including directory and single-file pseudo-archives. A backend SHALL NOT emit
duplicate warnings for one presentation of the same member.

#### Scenario: zip bomb extraction

- **WHEN** a zip bomb archive is extracted with default limits
- **THEN** extraction raises `ExtractionError` before the `max_extracted_bytes` or `max_ratio` threshold is exceeded

#### Scenario: path traversal member

- **WHEN** an archive containing a member named `../evil` is extracted
- **THEN** extraction raises `PathTraversalError` and no file is written outside the destination

#### Scenario: corrupt archive

- **WHEN** an archive with a truncated or CRC-invalid member is read
- **THEN** `CorruptionError` or `TruncatedError` is raised with the original exception attached as `__cause__`

#### Scenario: RTL warning is backend-independent

- **WHEN** any backend presents a member whose name contains U+202E RIGHT-TO-LEFT OVERRIDE
- **THEN** the member is rejected or exactly one warning is emitted for that presentation
