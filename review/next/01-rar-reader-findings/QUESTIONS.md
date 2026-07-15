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
> - **Caveat 2 (the wildcard corner — now fully characterized).** You asked whether
>   wildcard-char names are non-referable, whether positional args or `[]`/escaping could
>   address them. Tested exhaustively against RARLAB unrar 7.00:
>   - **Only `*` and `?` are mask metacharacters.** `?` matched one char (`-n./?anary.txt`
>     → `canary.txt`); `*` matched (`-n./can*`). Both work positionally too — positional
>     file args are *also* masks, so they give no exact-reference escape hatch.
>   - **`[ ]` are treated literally — unrar has NO character-class support.**
>     `-n./[c]anary.txt`, `-n./[abc]anary.txt`, `-n./[a-z]anary.txt` all returned exit 10
>     (no match). So `[*]`/`[?]` do **not** escape anything, and — the upside — a member
>     whose name literally contains `[` or `]` *is* addressable as-is (no special handling
>     needed). Backslash doesn't escape either (`-n./can\*` → exit 10; `\` is read as a
>     path separator).
>   - **Net:** there is **no escaping mechanism**. A member name containing a literal `*`
>     or `?` cannot be addressed to exactly one member via `unrar` — the mask will match
>     that member plus any others matching the pattern. (Such names are illegal on Windows
>     and rare/hostile elsewhere.)
>
>   **Recommendation:** for a name containing `*` or `?`, **fail the `unrar`-backed read
>   with a typed error** (e.g. `UnsupportedFeatureError`, "member name contains wildcard
>   characters unrar cannot address unambiguously") rather than risk emitting another
>   member's bytes. Optional refinement: if the mask matches *exactly one* known member
>   (compute against our parsed member list), allow it — but that means reproducing
>   unrar's glob semantics, so plain-fail is the simpler, safe default. Either way, keep
>   CRC + the Q3 length check as the backstop, and keep the original name in the listing.
>   `[`, `]`, `;`, spaces, unicode, etc. all need no special handling.
>   (One pre-existing, orthogonal edge: RAR5 names may contain a literal `\`, which the
>   parser does not fold to `/` — that already mismatches `unrar`'s path model on the
>   *current* positional path too, so it's not a regression from this change; worth a
>   follow-up.)

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
> Agreed: always verify length when we can (short → truncated/corrupt; long → corrupt;
> stop as soon as the declared size is exceeded). On *where*, you floated the top-level
> `ArchiveStream` vs the existing `VerifyingStream` vs a new class — I recommend the
> **top-level `ArchiveStream`** (the `_wrap_member_stream` wrapper), for a concrete
> reason:
>
> - **`VerifyingStream` is the wrong home** — it is only applied when `member.hashes` is
>   non-empty (`rar_reader.py:509`), so a CRC-less member (exactly the case that needs a
>   length backstop) never gets wrapped by it. Putting the check there would miss the gap.
> - **`ArchiveStream` already runs for every member and already carries `size`**
>   (`_wrap_member_stream(..., size=member.size)` → `ArchiveStream(..., size=size)`), so a
>   length check there is universal and format-agnostic — ZIP / 7z / tar get the same
>   guarantee for free. This matches "always verify when we can." So: **fold it into
>   `ArchiveStream`, not a new class**, enforced whenever `size is not None`.
>
> Two implementation notes to decide with it:
> 1. **Detecting *too long* needs the check to own the size bound.** On the RAR `unrar`
>    path the raw pipe is currently pre-clamped by `SlicingStream(length=size)`
>    (`rar_reader.py:580`), which silently truncates any excess — so an outer
>    `ArchiveStream` never sees the extra bytes. To honour "long → corrupt", let
>    `ArchiveStream` enforce the bound itself (read up to `size`, then confirm the
>    underlying is at EOF) and drop the bare size-clamping slice on that path. *Short*
>    detection works either way.
> 2. **Only enforce on forward-to-EOF consumption.** Mirror `VerifyingStream`: a caller
>    that deliberately reads part of a member and closes, or a seekable random-access read
>    that never touches the tail, must not trip the check. Enforce when the stream is
>    driven to EOF and the delivered length ≠ `size` (raise `TruncatedError` for short,
>    `CorruptionError` for long). For seekable direct reads where partial consumption is
>    normal, gate the check to the sequential/streaming path rather than every seek.
