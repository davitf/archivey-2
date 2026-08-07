# testing-contract — bidi both-branches delta

## MODIFIED Requirements

### Requirement: Adversarial corpus coverage

The system SHALL include an adversarial test corpus that exercises every documented
attack category and verifies that the correct exception is raised or limit is
enforced in each case. The required adversarial cases are:

| Case | Expected outcome |
| --- | --- |
| Zip bomb: quine-style and nested / 42.zip variant | `max_ratio` and `max_extracted_bytes` limits enforced before resource exhaustion |
| Ratio-floor false positive: tiny highly-compressible file (10 B -> 15 KiB, 1500:1) | Extracts without error while under `ratio_activation_threshold` |
| Path traversal: `../evil`, `../../etc/passwd`, `./../../outside` | `PathTraversalError`; no outside write |
| Absolute paths: `/etc/passwd`, `C:\Windows\System32\evil.dll` | `PathTraversalError` |
| Symlink escape: target `../../outside`, chained symlinks | `SymlinkEscapeError` |
| Symlink loop: cyclic `a -> b`, `b -> a` | `SymlinkEscapeError`; no uncaught `OSError` or crash |
| Corrupt archive: missing EOCD, truncated TAR, bad CRC | `CorruptionError` or `TruncatedError` with original cause attached |
| Unicode bombs: null bytes, bidi control characters | Null bytes rejected as traversal; a bidi control emits exactly one `MEMBER_NAME_BIDI_CONTROL` on listing, and an **override/isolate** additionally raises `DeceptiveNameError` on extraction |
| Giant claimed size: member claims 1 TiB while archive is 1 KiB | Extraction aborts cleanly before exhausting resources |

Regenerable adversarial archives SHALL be generated deterministically in memory or on
demand by `tests/create_adversarial.py` and SHALL NOT be committed. A hostile archive that
cannot be generated in the test environment MAY be committed under
`tests/fixtures/adversarial/` only with the fixture-policy JSON sidecar and an explicit
rationale.

The bidi-control outcome applies to every `ArchiveMember` presented by any backend,
including directory and single-file pseudo-archives. A backend SHALL NOT emit duplicate
diagnostics for one presentation of the same member.

**Listing and reading always present the name as stored.** Both branches are now
implemented and are named separately; the corpus SHALL cover each, and SHALL include at
least one **directional mark** case proving it is *not* rejected:

| Layer | Overrides / isolates (U+202A–202E, U+2066–2069) | Directional marks (U+061C, U+200E, U+200F) |
| --- | --- | --- |
| Listing / reading | Presented as stored; one `MEMBER_NAME_BIDI_CONTROL` | Presented as stored; one `MEMBER_NAME_BIDI_CONTROL` |
| Safe extraction | `DeceptiveNameError` from `check_universal`, hence a `BLOCKED` result, under every policy | Extracted normally |

#### Scenario: adversarial-behavior matrix

| Case | Expected |
| --- | --- |
| Zip bomb extracted with default limits | `ExtractionError` before configured byte or ratio limit is exceeded |
| Archive member named `../evil` is extracted | `PathTraversalError`; destination outside tree remains untouched |
| Truncated or CRC-invalid archive is read | `CorruptionError` or `TruncatedError`; original exception is `__cause__` |

#### Scenario: RTL warning is backend-independent

- **WHEN** any backend presents a member whose name contains U+202E RIGHT-TO-LEFT OVERRIDE
- **THEN** the name is presented as stored and exactly one `MEMBER_NAME_BIDI_CONTROL` diagnostic is emitted for that presentation

#### Scenario: directional marks are not swept into the rejection

- **WHEN** a member named with U+200F RIGHT-TO-LEFT MARK (a legitimate Arabic/Hebrew filename shape) is extracted
- **THEN** it extracts normally, proving the reject set is the override/isolate ranges and not the library's broader advisory set
