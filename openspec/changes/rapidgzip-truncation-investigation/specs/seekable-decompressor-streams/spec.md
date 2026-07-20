# seekable-decompressor-streams — rapidgzip truncation investigation delta

## MODIFIED Requirements

### Requirement: Accelerator backends surface corruption and truncation uniformly

The system SHALL surface corrupt or truncated input read through the rapidgzip accelerator as the
same `compressed-streams` error types as the stdlib path (`CorruptionError` / `TruncatedError`),
never a raw third-party exception. For truncation specifically, the system SHALL rely on
rapidgzip's own end-of-input errors where it raises them. Where rapidgzip reaches EOF having
delivered **no** decompressed bytes without raising, the system SHALL fall back to the stdlib
gzip path (sized reads) so truncation is signaled and any recoverable prefix is available.
Where rapidgzip delivered a non-empty prefix (or full payload) and reached EOF without raising,
the system SHALL apply a length/ISIZE backstop that covers those characterized silent cases
without ever false-flagging a valid file; multi-member scope SHALL be stated explicitly
(safe per-member ISIZE sum, not “any further header ⇒ accept”).

Priorities for this path: (1) no silent success, (2) recover partial data where stdlib can,
(3) retain rapidgzip seekability on intact inputs. DIY reverse deflate-block seeking from the
gzip trailer is out of scope (trailer is CRC+ISIZE only).

#### Scenario: a truncation rapidgzip reports itself

- **WHEN** a truncated gzip is read through rapidgzip and rapidgzip raises its own end-of-input error
- **THEN** that error is translated to `TruncatedError` (or `CorruptionError`), with no reliance on the ISIZE backstop

#### Scenario: silent empty EOF from rapidgzip

- **WHEN** a truncated gzip is read through rapidgzip and the first EOF delivers zero decompressed bytes without an exception
- **THEN** the system falls back to stdlib gzip sized-reads on the same source, surfaces `TruncatedError` (from stdlib `EOFError`), and exposes any correct partial prefix stdlib recovered
- **WHEN** the input is a valid empty gzip member
- **THEN** both rapidgzip and the stdlib fallback succeed with zero bytes (no false truncation)

#### Scenario: silent short or full EOF from rapidgzip

- **WHEN** a truncated gzip is read through rapidgzip and a non-empty decompressed prefix (or full payload) is returned without an exception
- **THEN** the ISIZE/length backstop raises `TruncatedError` at sequential EOF, and a valid single- or multi-member file is never false-flagged
