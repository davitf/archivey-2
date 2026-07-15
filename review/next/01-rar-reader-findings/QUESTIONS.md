# Questions for the maintainer — RAR reader review

Three decisions the review can't settle without you. Per `CLAUDE.md` I'm surfacing
rather than silently resolving them.

## Q1 — Wrong-password header decryption: `EncryptionError` or `CorruptionError`? (F1)

For RAR3 (and RAR5 without a check value) a wrong password and genuine corruption are
**cryptographically indistinguishable** — both produce a garbage decrypted header.
Today the code lands on `CorruptionError`, which (a) contradicts the error-contract
expectation that a wrong-password header is `EncryptionError`, and (b) escapes the
`_PasswordCandidates.attempt` retry loop (it only catches `EncryptionError`), so a
candidate list `["wrong", "correct"]` aborts instead of trying `"correct"`.

**My recommendation:** when `has_header_encryption` is set and a password was
supplied, map a post-decrypt structural failure to `EncryptionError` (favouring "your
password was probably wrong" over "the archive is corrupt"), so candidate iteration
and the provider flow work. The cost: a genuinely-corrupt header on a correct password
would be reported as `EncryptionError` instead of `CorruptionError`.

Do you want (a) that mapping, (b) keep `CorruptionError` but teach `attempt` /
`_parse_archive` to treat it as a candidate failure for RAR-header decryption, or
(c) something else? This decision also touches Brief 2's crypto seam — worth
coordinating so both briefs land the same contract.

## Q2 — `unrar` argv hardening for hostile member names (F3)

The spec's "Constrain unrar argv by call site" requirement forbids the *backend* from
passing globs/`@listfile`, but a hostile archive can *name a member* `@listfile` or
`-somebswitch`, and that name reaches `unrar` unescaped. Minimal hardening is `"--"`
before the archive path (stops switch parsing) plus rejecting/neutralizing `@`-leading
member names (`--` does not stop `unrar`'s `@`-listfile expansion).

Is inserting `"--"` + rejecting `@`-leading names on the `unrar` path the fix you
want, or do you prefer to normalize/escape names elsewhere? I can't verify the exact
`unrar` behaviour here (binary not installed) — do you want me to hold the fix until
it's validated against a real RARLAB `unrar`, or land the defensive `--` now?

## Q3 — `unrar` non-11 exit codes and short output (F4)

Only exit code 11 (bad password) is translated; codes 2 (fatal), 3 (CRC/corrupt), and
10 (no files matched) are dropped, and the nonsolid single-member `SlicingStream`
never checks it received `size` bytes — so a mis-parsed/corrupt member without a
stored CRC yields a silent short/empty stream.

Proposed mapping: `3 → CorruptionError`, `2 → CorruptionError` (or a generic
`ArchiveError`), `10 → CorruptionError`/`TruncatedError` (member vanished), plus a
length assertion on the nonsolid path mirroring the solid path's `EOFError →
TruncatedError`. Does that match how you want `unrar` failures surfaced, and are there
other codes (e.g. 9 create-error — shouldn't occur under `p`) you want mapped? Again,
worth confirming against a real `unrar` before I encode specific numbers.
