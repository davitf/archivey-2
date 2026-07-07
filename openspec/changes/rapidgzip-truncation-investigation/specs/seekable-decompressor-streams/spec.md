# seekable-decompressor-streams — rapidgzip truncation investigation delta

## MODIFIED Requirements

### Requirement: Accelerator backends surface corruption and truncation uniformly

The system SHALL surface corrupt or truncated input read through the rapidgzip accelerator as the
same `compressed-streams` error types as the stdlib path (`CorruptionError` / `TruncatedError`),
never a raw third-party exception. For truncation specifically, the system SHALL rely on
rapidgzip's own end-of-input errors where it raises them, and SHALL apply a backstop **only** for
the characterized cases where rapidgzip silently returns short/zero output. The backstop SHALL be
the narrowest check that covers those cases without ever false-flagging a valid file, and its
scope (single-member vs. multi-member) SHALL be stated explicitly rather than implied.

#### Scenario: a truncation rapidgzip reports itself

- **WHEN** a truncated gzip is read through rapidgzip and rapidgzip raises its own end-of-input error
- **THEN** that error is translated to `TruncatedError` (or `CorruptionError`), with no reliance on the ISIZE backstop

#### Scenario: a truncation rapidgzip does not report

- **WHEN** a truncated gzip is read through rapidgzip in a characterized silent-truncation case (e.g. a bare-header-only input)
- **THEN** the backstop raises `TruncatedError`, and the check is scoped so a valid single- or multi-member file is never false-flagged
