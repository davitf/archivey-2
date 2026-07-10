# compressed-streams — integrity and stream diagnostics

## MODIFIED Requirements

### Requirement: Verify decompressed output against expected digests

The verification stage SHALL continue to raise `CorruptionError` for a computable digest
mismatch at clean EOF and skip verification after a partial read. It SHALL compute each
available algorithm incrementally over decompressed bytes. A mismatch SHALL surface from
the terminal read that would otherwise signal EOF, after every data chunk has been
delivered; a bytes-returning full read SHALL raise and return no bytes. Codec-internal
checks remain distinct from this container-supplied digest stage, and random-access/
partial reads do not verify.

When an expected digest algorithm cannot be computed because it is unknown or its backend
is unavailable, the stage SHALL emit `DIGEST_UNVERIFIABLE` with typed context containing
the algorithm, non-secret reason, and member identity when available.

The event SHALL follow diagnostic policy. Under `COLLECT`, that algorithm is skipped while
other computable algorithms are still verified; the occurrence appears in the stream/
reader aggregate and MAY attach to the member under the shared retention budget. Under
`IGNORE`, it is counted but has no delivery/detail and verification still skips it. Under
`RAISE`, `DiagnosticRaisedError` halts the read.

#### Scenario: unverifiable digest is collected as data

- **WHEN** a member's expected `blake2sp` cannot be computed and default policy applies
- **THEN** `DIGEST_UNVERIFIABLE` is counted/retained/logged, may attach to the member, and the readable bytes are returned without that digest check

#### Scenario: digest mismatch on full read

- **WHEN** a member is read to EOF and its decompressed bytes do not match an expected computable digest
- **THEN** `CorruptionError` naming the algorithm is raised

#### Scenario: mismatch does not discard the final chunk

- **WHEN** a caller consumes chunks until EOF from a member whose digest mismatches
- **THEN** every data chunk is delivered, and the following terminal read raises `CorruptionError`

#### Scenario: partial read is not verified

- **WHEN** a caller abandons a member stream before clean EOF
- **THEN** no digest verdict or mismatch exception is produced

#### Scenario: strict caller escalates unverifiable digest

- **WHEN** `DIGEST_UNVERIFIABLE` resolves to `RAISE`
- **THEN** `DiagnosticRaisedError` halts opening/reading the stream rather than silently skipping the check

## ADDED Requirements

### Requirement: Public ArchiveStream exposes bounded operation snapshots

Every public `ArchiveStream` SHALL expose an immutable `diagnostics` snapshot. For a
reader-owned stream this is an operation-filtered view over the reader collector; for a
standalone codec stream it is a stream-lifetime collector. Serving the view SHALL not
retain a second aggregate copy of each occurrence.

#### Scenario: standalone stream owns its diagnostics

- **WHEN** a standalone codec stream emits an index or rewind diagnostic
- **THEN** `stream.diagnostics` exposes exact counts and bounded retained details without requiring an `ArchiveReader`
