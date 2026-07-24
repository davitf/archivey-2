# seekable-decompressor-streams — rapidgzip truncation investigation delta

## MODIFIED Requirements

### Requirement: Accelerator backends surface corruption and truncation uniformly

The system SHALL surface corrupt or truncated input read through the rapidgzip accelerator as the
same `compressed-streams` error types as the stdlib path (`CorruptionError` / `TruncatedError`),
never a raw third-party exception.

Upstream rapidgzip (0.16.0 / librapidarchive) treats many incomplete streams as **soft EOF by
design** (parallel trial-and-error decode): `read()` may return empty or a short/full prefix
with **no** Python exception, and may report `block_offsets_complete=True` /
`size == len(returned)`. See `docs/internal/rapidgzip-upstream-report.md`. The system SHALL
**not** treat rapidgzip exceptions alone, stderr, or `block_offsets_complete` / `size` as
sufficient completeness signals. (Stderr `Unexpected end of file when getting block…` is logged
on a **rethrow** path near the trailer, not on common silent-empty success — do not parse it.)

For truncation specifically (locked stack):

1. **WHEN** rapidgzip raises — translate to `TruncatedError` / `CorruptionError` (sandbox /
   timeout still required: some raises are followed by `std::terminate`).
2. **WHEN** rapidgzip reaches EOF having delivered **no** decompressed bytes without raising —
   fall back to stdlib gzip **before** returning that empty EOF to the caller (sized reads), so
   truncation is signaled and any recoverable prefix is streamed; a valid empty gzip SHALL still
   succeed with zero bytes.
3. **WHEN** rapidgzip delivered a non-empty prefix (or full payload) and reached EOF without
   raising — apply a length/ISIZE backstop at sequential EOF for **single-member** gzip that
   covers those silent cases without false-flagging a valid file.

**Multi-member scope (deferred):** concatenated multi-member gzip keeps today’s conservative
rule (any further `1f 8b 08` ⇒ do not raise on ISIZE mismatch). A safe per-member ISIZE sum is
**out of scope** for this change: locating members requires a forward magic scan with the same
false-header ambiguity the current bailout already accepts (false-negative only). Document that
limitation explicitly when syncing the main spec.

Priorities: (1) no silent success, (2) recover partial data where stdlib can, (3) retain
rapidgzip seekability and all-cores parallelization on intact inputs (`parallelization=0` is
intentional — upstream maps it to all cores). DIY reverse deflate-block seeking from the gzip
trailer is out of scope. Do **not** use `tell_compressed()` bit offsets as a completeness
heuristic (not a stable cross-file constant).

#### Scenario: a truncation rapidgzip reports itself

- **WHEN** a truncated gzip is read through rapidgzip and rapidgzip raises its own end-of-input error
- **THEN** that error is translated to `TruncatedError` (or `CorruptionError`), with no reliance on the ISIZE backstop

#### Scenario: silent empty EOF from rapidgzip

- **WHEN** a truncated gzip is read through rapidgzip and the first EOF would deliver zero decompressed bytes without an exception
- **THEN** the system fully switches to the stdlib gzip-window `DecompressorStream` on the same source **before** returning empty success to the caller, surfaces `TruncatedError` from that engine's read path (ADR 0014 — not from `close()`), and streams any correct partial prefix on bounded `read(n)` (`read()` / `readall` raise without returning bytes)
- **WHEN** the input is a valid empty gzip member
- **THEN** both rapidgzip and the stdlib fallback succeed with zero bytes (no false truncation)

#### Scenario: silent short or full EOF from rapidgzip (single-member)

- **WHEN** a truncated **single-member** gzip is read through rapidgzip and a non-empty decompressed prefix (or full payload) is returned without an exception
- **THEN** the ISIZE/length backstop raises `TruncatedError` at sequential EOF, and a valid single-member file is never false-flagged

#### Scenario: multi-member ISIZE mismatch (deferred)

- **WHEN** the ISIZE trailer does not match the decompressed total and a further gzip magic is found in the file
- **THEN** the system does **not** raise from the ISIZE backstop (conservative false-negative; per-member ISIZE summing is deferred)

#### Scenario: rapidgzip completeness APIs are not trusted

- **WHEN** rapidgzip reports `block_offsets_complete=True` and/or `size == len(data)` after a silent short or empty read of a truncated file
- **THEN** the system still applies the empty→stdlib and/or ISIZE backstops; those API flags alone SHALL NOT clear truncation
