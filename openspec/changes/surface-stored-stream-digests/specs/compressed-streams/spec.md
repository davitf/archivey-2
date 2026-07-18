## MODIFIED Requirements

### Requirement: Decompressed output digests are verified at clean EOF

The verification stage SHALL compute available expected digest algorithms
incrementally over decompressed bytes and raise `CorruptionError` for a
computable mismatch at clean EOF. A mismatch SHALL surface from the terminal read
after all data chunks have been delivered; a bytes-returning full read raises and
returns no bytes. Partial/random-access reads SHALL NOT produce a digest verdict.

Supported computable algorithms SHALL include `crc32` (via `zlib.crc32`),
`adler32` (via `zlib.adler32`), the `hashlib.algorithms_available` set, and
`blake2sp` (the 8-way parallel BLAKE2s tree hash used by RAR5), computed via an
internal zero-dependency hasher. A well-formed member carrying only a `blake2sp`
digest SHALL therefore be verified, not skipped. A well-formed zlib member
carrying only `adler32` SHALL likewise be verified, not skipped.

When an expected digest cannot be computed because the algorithm is genuinely unknown
or a backend is missing, the system SHALL emit `DIGEST_UNVERIFIABLE` with algorithm,
non-secret reason, and member identity when available. Diagnostic policy controls
collection, logging/callback delivery, member attachment, and escalation.

#### Scenario: digest matrix

| Case | Expected |
| --- | --- |
| Expected `blake2sp` on a well-formed RAR5 member | Computed and verified; mismatch raises `CorruptionError` |
| Expected `adler32` on a well-formed zlib member | Computed and verified; mismatch raises `CorruptionError` |
| Expected digest under a genuinely-unknown algorithm name | `DIGEST_UNVERIFIABLE` counted/retained/logged; bytes still returned without that check |
| Full member read reaches EOF with computable digest mismatch | `CorruptionError` naming the algorithm |
| Chunked read reaches EOF with mismatch | All valid chunks delivered; following terminal read raises |
| Caller abandons stream before clean EOF | No digest verdict or mismatch exception |
| Unverifiable digest resolves to `RAISE` | `DiagnosticRaisedError` halts open/read |
