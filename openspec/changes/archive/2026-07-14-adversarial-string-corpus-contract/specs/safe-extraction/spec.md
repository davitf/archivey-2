# Safe Extraction — delta (adversarial-string-corpus-contract)

The corpus's NUL-in-link-target case is enforced by `check_universal`'s string-level
guards, whose requirement lives in the `hypothesis-property-tests` change
("Unrepresentable names and link targets are rejected as typed errors"); this change
only exercises it. The requirement below covers the remaining raw-exception path the
corpus exposed: a name that passes every string check but that the target filesystem
itself refuses.

## ADDED Requirements

### Requirement: Filesystem refusal of a member name is a typed error

A member name can pass `check_universal` (it encodes via `os.fsencode`, e.g. undecodable
archive bytes carried as `surrogateescape` low surrogates) and still be refused by the
destination filesystem at write time — a UTF-8-enforcing filesystem (APFS) rejects the
byte sequence with `EILSEQ`. Extraction SHALL translate that refusal into a typed
`ExtractionError` (carrying the member name and the original `OSError` as cause) rather
than letting the raw `OSError` escape. Under `OnError.CONTINUE` it is an ordinary
per-member failure result. On filesystems that accept arbitrary bytes (typical Linux),
the member extracts normally; the refusal is an environment outcome, not a property of
the archive.

(Implemented on main via `hypothesis-property-tests`; this change's corpus exercises it.
`EINVAL` is deliberately not auto-translated: it is a broad errno that can arise from
unrelated syscalls during extraction.)

Renaming the member to a representable name instead of failing is deliberately not part
of this requirement — it belongs to the future opt-in `SANITIZE` extraction policy
(post-v1, see `IDEAS.md`), not to a bespoke option.

#### Scenario: UTF-8-enforcing filesystem refuses a surrogateescape name

- **WHEN** a member whose name carries undecodable bytes (`surrogateescape`) is extracted
  to a filesystem that enforces valid UTF-8 names
- **THEN** extraction raises a typed `ExtractionError` whose cause is the filesystem's
  `OSError` with `EILSEQ` (never a raw `OSError`), or records a failure result under
  `OnError.CONTINUE`

#### Scenario: byte-preserving filesystem extracts the same member

- **WHEN** the same member is extracted on a filesystem that accepts arbitrary name bytes
- **THEN** the member extracts successfully with its bytes preserved
