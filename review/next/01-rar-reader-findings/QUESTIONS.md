# Questions for the maintainer — RAR reader review

Three decisions the review can't settle without you. Per `CLAUDE.md` I'm surfacing
rather than silently resolving them.

> **Status: all three answered by the maintainer (2026-07-15).** Resolutions and the
> `unrar`-behaviour testing that backs them are recorded under each question. Ready to
> implement on request — see the "Resolution" blocks for the two remaining judgment
> calls (Q2 wildcard corner, Q3 strict-length placement).

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

> **Resolution — map to `EncryptionError` (option a), but only where no verifier
> exists.** The maintainer asked whether any fixed header structure could positively
> indicate *corruption* rather than a wrong password. Answer: **the only such signal is
> the RAR5 password check value**, and where it exists the code already does the right
> thing —
>
> - **RAR5 *with* a check value** (`_check_rar5_password`, the common case): a wrong
>   password is caught up front as `EncryptionError` *before* decryption. If that check
>   passes, the AES key is proven correct, so a subsequent header-CRC failure genuinely
>   *is* corruption and `CorruptionError` is the correct label. **No change here.**
> - **RAR3 (never has a verifier) and RAR5 *without* a check value:** there is no MAC,
>   magic, or check field — a wrong key and corrupted ciphertext both decrypt to
>   uniformly random bytes that fail the block CRC. They are information-theoretically
>   indistinguishable, so the decrypted `block_type` / `header_size` are *not* a usable
>   "this is corruption" signal (both are random). Here, when a password was supplied,
>   map the post-decrypt structural/CRC failure to `EncryptionError`.
>
> So the fix is targeted: widen the wrong-password → `EncryptionError` mapping to cover
> the RAR3 post-decrypt block read + CRC (`rar_parser.py:830-883`) and the checkval-less
> RAR5 `_read_rar5_block` call (`rar_parser.py:1197`), leaving the verified-RAR5 path
> raising `CorruptionError`. That restores candidate iteration and matches the contract,
> while still reporting genuine corruption precisely wherever RAR gives us the means to
> know. Coordinate the exact seam with Brief 2 (crypto).

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

> **Resolution — there IS a simple, uniform escaping: the `-n` include-mask switch
> with a `./` prefix.** The maintainer asked whether a legitimate escaping exists to
> refer to `@`-named members, or whether they're fundamentally unreferenceable, and said
> the `unrar` call is the right place while member *listing* keeps the original names.
> Tested against RARLAB unrar 7.00 (see `unrar-boundary.md`):
>
> - Replacing the positional member arg with **`-n./<member>`** (an include mask whose
>   value starts with `.`) neutralizes *both* hostile prefixes in one move — the leading
>   `-` isn't a switch (it's inside the `-n` value) and the leading `@` isn't a listfile
>   (the value starts with `.`, not `@`). Confirmed exact-member extraction for
>   `-n./-inul`, `-n./@atfile`, `-n./canary.txt`, and `-n./subdir/file2.txt`. `--` alone
>   was *not* enough (it fixes `-` but not `@`); `-n./` is the simple solution that
>   covers both, so we do **not** need to reject `@` names.
> - **Caveat 1 (version history):** the `-n` mask excludes `-ver` history rows unless
>   `-ver` is also passed (positional worked without it, but `-n./file.txt;1` needed
>   `-ver`). So add `-ver` to the named per-member call when the target
>   `is_file_version_history()` — reuse the existing `version_control` plumbing.
> - **Caveat 2 (the one judgment call):** `-n` values are *masks*, so literal wildcard
>   metacharacters (`* ? [ ]`) in a member name are interpreted as wildcards and could
>   match the wrong/multiple members (`-n./*` matched all three members). Archivey's
>   existing CRC verification plus the Q3 length check already make a mis-match surface
>   as `CorruptionError` rather than silent wrong bytes, so it's *safe* by default. Do
>   you also want a precise up-front error (reject names containing raw `* ? [ ]` on the
>   `unrar` path), or is "caught by CRC/length" enough? Member listing keeps the
>   original names either way, as you asked.

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

> **Resolution — mappings agreed; add the short-read check as an opt-in, not a global
> `SlicingStream` change.** The maintainer agreed with the exit-code mappings and asked
> whether `SlicingStream` should itself raise a truncation/corruption error when the
> underlying ends before the declared `length`.
>
> Yes in spirit, but **not by changing `SlicingStream`'s default**: its short-read
> *clamp* is a deliberate BytesIO-compatible contract (`slice.py:200` "reads clamp to
> empty, matching BytesIO") and it's the shared slice used by ZIP, 7z and RAR
> (`grep SlicingStream(` → 11 call sites). Some callers legitimately over-declare a
> length or slice open-endedly, so making every slice raise on a short underlying could
> regress them. Instead add an **opt-in** strict mode — a `min_length` / `strict=True`
> flag on `SlicingStream`, or a tiny length-verifying wrapper analogous to
> `VerifyingStream` — and use it on the `unrar`-backed RAR path (and anywhere a declared
> size is a hard contract). It would raise `TruncatedError` (or `CorruptionError`) once
> the stream ends short of the declared size, i.e. "after the last actual byte", exactly
> as you described. As you noted it doesn't change *this* finding (CRC already catches
> the hostile-name case), but it's good defense-in-depth for CRC-less members and for
> the exit-2/3/10 paths where `unrar` stops early. Which do you prefer — a `strict` flag
> on `SlicingStream`, or a separate `LengthVerifyingStream` wrapper?
