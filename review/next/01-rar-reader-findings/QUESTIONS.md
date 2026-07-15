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
`-someswitch`, and that name reaches `unrar` unescaped. **Verified against RARLAB
unrar 7.00** (`repro.py` F3b): a `-`-named member is parsed as a switch and makes
`unrar` emit *all* members' data (wrong bytes for the requested member); an
`@`-named member makes `unrar` read an arbitrary local file. `"--"` before the member
argument neutralizes the switch case (`-- -inul` → exit 10, empty) but does **not**
stop `@`-listfile expansion (`-- @list` still read the file).

So the fix is two parts that must land together: insert `"--"` **and** reject (or
neutralize) `@`-leading member names on the `unrar` path. Is that the shape you want,
or would you rather normalize/escape names further upstream? Both parts are now
validated here, so I can land them without waiting on further `unrar` checks — say
the word.

## Q3 — `unrar` non-11 exit codes and short output (F4)

Only exit code 11 (bad password) is translated; codes 2 (fatal), 3 (CRC/corrupt), and
10 (no files matched) are dropped, and the nonsolid single-member `SlicingStream`
never checks it received `size` bytes — so a mis-parsed/corrupt member without a
stored CRC yields a silent short/empty stream.

Codes **3 (CRC/corrupt)** and **10 (no files matched)** are confirmed here against
RARLAB unrar 7.00 (`repro.py`: a byte-flipped archive → exit 3; a non-matching name →
exit 10, empty output). Proposed mapping: `3 → CorruptionError`, `2 → CorruptionError`
(or a generic `ArchiveError`), `10 → CorruptionError`/`TruncatedError` (member
vanished), plus a length assertion on the nonsolid path mirroring the solid path's
`EOFError → TruncatedError`. Does that match how you want `unrar` failures surfaced,
and are there other codes (e.g. 9 create-error — shouldn't occur under `p`) you want
mapped?
