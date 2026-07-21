## ADDED Requirements

### Requirement: Content verification runs in a selectable mode

`ArchiveyConfig` SHALL expose a `verification_mode` (`VerificationMode`) with at
least `STREAMING` (default) and `STRICT`. The mode governs decompressed-content
verification — digest/CRC, declared length, and encrypted-member authentication
tags — **uniformly across formats** (a digest member and an encrypted member
behave the same for a given mode).

**`STREAMING` (default).** Verification is sequential and lazy: a content verdict
(`CorruptionError` for a digest/auth mismatch, `TruncatedError` for a hash-less
short) SHALL surface only from the read that completes the stream (the terminal
empty `read`, or `read(-1)` / `readall`). A partial read, a seek off the
sequential frontier, or a read-then-close that never reaches the completing read
SHALL **abandon** verification with no verdict. `close()` SHALL NOT surface a
first content fault — **including encrypted members**. An encrypted member's
authentication tag SHALL still be verified when a full read consumes the
authenticating bytes, but SHALL NOT be force-drained and verified from `close()`.

**`STRICT`.** Verification SHALL be guaranteed regardless of access pattern,
uniformly for digest and auth-tag members:

- A member whose integrity cannot be confirmed SHALL NOT silently yield bytes that
  are then trusted: on a partial read STRICT SHALL force a full verifying pass and
  raise on a corrupt/tampered/short member, subject to `extraction_limits` /
  output caps (a bounded verify-ahead, never an unbounded slurp).
- A seek that would disable frontier verification SHALL first force a full
  verifying pass, or SHALL fail the seek with a typed error — never silently drop
  the check.
- `close()` after a partial read SHALL complete verification (drain to EOF and run
  the verdict) — this is the mode's behavior applied to every integrity check,
  replacing any per-format close-time authentication.

STRICT MAY require a full decompress/decrypt ahead of use and therefore MAY exceed
the ≤~1.3× stdlib budget; this cost SHALL be documented and STRICT SHALL never be
selected implicitly.

#### Scenario: verification mode matrix

| Case | STREAMING (default) | STRICT |
| --- | --- | --- |
| Full read of a good member | Verdict on completing read; passes | Passes |
| Full read of a corrupt member | `CorruptionError` on completing read | `CorruptionError` |
| Partial read then close, corrupt member | No verdict (quiet) | Full verifying pass; raises `CorruptionError` |
| Seek off frontier then read, corrupt member | No verdict (verification disabled) | Full verifying pass first, or seek fails with a typed error |
| Encrypted member, full read, bad HMAC | `CorruptionError` on completing read | `CorruptionError` |
| Encrypted member, partial read then close, bad HMAC | No verdict (quiet); `close()` does not drain/authenticate | Full verifying pass; raises `CorruptionError` |
| STRICT verify-ahead vs. a decompression bomb | n/a | Bounded by `extraction_limits`; over-cap raises, never unbounded slurp |

## MODIFIED Requirements

### Requirement: Decompressed output digests are verified at clean EOF

The verification stage SHALL compute available expected digest algorithms
incrementally over decompressed bytes and raise `CorruptionError` for a
computable mismatch at clean EOF. In the default `STREAMING` mode a mismatch SHALL
surface from the read that completes the stream (the terminal empty `read`, or a
bytes-returning full `read(-1)` / `readall`, which raises and returns no bytes);
partial/random-access reads SHALL NOT produce a digest verdict, and `close()`
SHALL NOT be the sole surface for a digest or short-length verdict. This
lazy-abandon behavior is the `STREAMING` contract; `STRICT` guarantees a verdict
regardless of access pattern (see "Content verification runs in a selectable
mode"). The timing and close rules apply **uniformly to encrypted-member
authentication tags**: an auth-tag mismatch is a content fault subject to the same
mode contract, and MUST NOT be surfaced as a first content fault from `close()` in
`STREAMING`.

Supported computable algorithms SHALL include `crc32` (via `zlib.crc32`),
`adler32` (via `zlib.adler32`), the `hashlib.algorithms_available` set, and
`blake2sp` (the 8-way parallel BLAKE2s tree hash used by RAR5), computed via an
internal zero-dependency hasher. A well-formed member carrying only a `blake2sp`
digest SHALL therefore be verified, not skipped. When an expected `adler32` is
installed on a verifying stream, it SHALL likewise be computed and checked (not
skipped as unknown).

When an expected digest cannot be computed because the algorithm is genuinely unknown
or a backend is missing, the system SHALL emit `DIGEST_UNVERIFIABLE` with algorithm,
non-secret reason, and member identity when available. Diagnostic policy controls
collection, logging/callback delivery, member attachment, and escalation.

#### Scenario: digest matrix

| Case | Expected |
| --- | --- |
| Expected `blake2sp` on a well-formed RAR5 member | Computed and verified; mismatch raises `CorruptionError` |
| Expected `adler32` on a verifying stream | Computed and verified; mismatch raises `CorruptionError` |
| Expected digest under a genuinely-unknown algorithm name | `DIGEST_UNVERIFIABLE` counted/retained/logged; bytes still returned without that check |
| Full member read reaches EOF with computable digest mismatch | `CorruptionError` naming the algorithm |
| Chunked read reaches EOF with mismatch | All valid chunks delivered; following terminal read raises |
| Caller abandons stream before clean EOF (`STREAMING`) | No digest verdict or mismatch exception |
| Caller abandons stream before clean EOF (`STRICT`) | Full verifying pass forced; mismatch raises |
| Encrypted-member auth-tag mismatch, partial read then close (`STREAMING`) | No verdict; `close()` does not authenticate |
| Unverifiable digest resolves to `RAISE` | `DiagnosticRaisedError` halts open/read |
