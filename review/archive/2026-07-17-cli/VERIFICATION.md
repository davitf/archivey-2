> **Archive note (2026-07-17):** R1–R4 below were fixed on PR #120 before merge;
> this file is the mid-pass verification snapshot. See `review/README.md` archive
> table for the final outcome.

# Brief 4 — verification of the fix pass (PR #120 @ `b75aacc`)

Re-review of the follow-up commits `3453e5c` (F1–F12) and `660f7ed` (D1–D8) plus
`b75aacc` (IDEAS.md parking), commissioned after the implementor's status
comment on PR #131. Everything below was re-verified by running the CLI and the
full test suite from the updated branch (Python 3.11.15, `--extra all`).

## Verdict

**The findings pass is genuinely good — F1–F12 and D2–D8 are all correctly
implemented and tested — but the branch is not mergeable yet.** One fix (D8)
regressed a library test that fails in every `[all]` CI job (R1), the F5 fix
missed the subparsers (R2), the D4 logging fix surfaced a pre-existing
library-side warning spam on ordinary tars (R3), and the D1 "no-index →
always-wrap" catch is a spec-level behavior change vs. the merged #110 proposal
that needs an explicit maintainer sign-off (R4).

## Confirmed fixed (re-tested end-to-end)

| Item | Evidence |
| --- | --- |
| F1 pre-verb globals | `archivey --track-io a.zip` reports; `archivey --password secret t enc.7z` decrypts (`1 OK, 0 failed`); post-verb still overrides pre-verb. Two-instance `SUPPRESS` parents exactly as recommended; regression tests added, incl. the namespace-capture test. |
| F2 broken pipe | `archivey l big.zip \| head -1` → exit 0, empty stderr; handler reordered above `OSError` + `_silence_broken_pipe()` flush guard. |
| F3/D2 extraction reporting | `renamed: out/a.txt -> out/a (1).txt` always printed; closing summary `2 extracted, 1 renamed, 0 skipped → out/` always printed; `-v` adds per-member `extracted:` lines. `ExtractionReport` consumed; `verbose` no longer `del`'d. |
| F4/D3 test summary on open failure | `archivey t enc.7z` (no password) → `FAIL: …` + `0 OK, 1 failed`, exit 1. Manual `next()` loop catches per-advance errors; generator-death limitation documented in code + IDEAS.md. |
| F5 abbreviation (pre-verb) | `archivey --pass secret a.zip` → clean `unrecognized arguments: --pass`, exit 2. **But see R2** — subparsers still abbreviate. |
| F6 bare `-` | `archivey -` → the friendly reserved-stdin message (exit 2, per D7). |
| F7 Ctrl-C | `except KeyboardInterrupt → "interrupted", 130` correctly wraps `_dispatch` (verified by inspection; disjoint from the other handlers). |
| F8 stem | `file_extension()`-based strip + generic suffix fallback; `.tar.gz`-named file → `"archive"`, `data.tar.Z` → `data`; unit-tested. |
| F9 vacuous test | Now patches `archivey.cli.extract_cmd.make_progress_callback` (the consumed binding). |
| F10 OSError stop notice | `except (ArchiveyError, OSError)` shares the "extraction stopped" message. |
| F11 bar lifecycle | `ProgressCallback` class with idempotent `close()`, called in `finally` by both extract and test. |
| F12 nits | info aligned + human format labels (`ZIP`, not `ArchiveFormat.ZIP`); track-io prints `-` for None; container rename now `stem (1)` matching library style; test counts files only (dirs/links → verbose `skip` lines); `fnmatchcase`; `out`/`err` threaded through `_dispatch` into every verb; `--track-io` on info prints an explicit `n/a`. |
| D1 filtered tops | `archivey x pack.zip 'b/*'` (multi-top zip, single filtered root) → `./b/…`, no wrapper; tested. No-index case → always wrap; tested. **See R4 for the sign-off question.** |
| D4 logging | `WARNING: Member name normalized: …` properly formatted via the `archivey` logger handler; `-v` → INFO. No caplog/propagate fallout anywhere in the suite. **See R3.** |
| D5 test progress | Bar driven from synthesized `ExtractionProgress` (cumulative bytes, file-only totals, totals only when all sizes known); closed in `finally`; smoke-checked on a fake TTY. |
| D6 `cat` reserved | Verb table, help, spec + permanence sentence; tested. |
| D7 exit 2 | `-` and `--salvage` now exit 2 like reserved verbs; tests updated. |
| D8 message split | `archivey t enc.7z --password wrong` → `Wrong password or corrupt 7z folder`; no password → `Password required…`; unit test covers required-vs-rejected. **But see R1.** |

Gates on `b75aacc`: `ruff check` + `ruff format --check` clean, `pyrefly` 0
errors, `ty` all-pass, `openspec validate --strict cli-v1` clean. Full pytest:
**1686 passed, 1 failed** — the single failure is R1, and it is exactly what CI
shows (every `[all]` job red with only that test; `core-only` green because the
test needs py7zr).

## Remaining issues

### R1 — **Blocks merge.** D8 regressed the 7z header-encrypted error message

`sevenzip_reader.py` `_decrypt_header` now re-raises
`_PasswordCandidatesExhausted.message` verbatim. With no candidates supplied
that message is the generic `Password required to read this encrypted member`,
so the header path lost its "7z header" context and
`test_sevenzip_reader.py::test_header_encrypted_archive_requires_password`
(`match="header"`) fails — the only red test, in every `[all]` CI job on every
OS/Python. The old message was also better UX: it tells the user the *listing*
needs a password, not some member.

**Fix shape:** keep the required-vs-rejected split but restore the surface in
the header path — e.g. required → `Password required to decrypt the 7z header`,
rejected → `Password(s) rejected for the 7z header` (rewrite
`…read this encrypted member` → `…the 7z header`, or pass a
surface label down to `attempt()`). Leave the member path as-is.

### R2 — F5 incomplete: post-verb abbreviation still enabled

`allow_abbrev=False` was set on the top-level parser and on both parent
instances, but `sub.add_parser(...)` does **not** inherit it (argparse default
`True` per parser). Verified: `archivey x a.zip -d out --over error` silently
abbreviates `--overwrite`. Harmless today, but it un-stabilizes the script
grammar (any future `--over*` flag turns existing scripts ambiguous — exactly
what F5 was about). Pass `allow_abbrev=False` in every `sub.add_parser(...)`
call; add one post-verb abbreviation test.

### R3 — D4 surfaced library warning spam: one WARNING per tar directory

Python's `tarfile` strips the trailing slash from directory member names, so
`presented_name` (`pkg`) always differs from the normalized name (`pkg/`) and
`emit_member_name_normalized` fires for **every directory in every ordinary
tar**:

```
$ archivey l wt.tar          # tar with 3 dirs, made by GNU tar
WARNING: Member name normalized: 'pkg' -> 'pkg/'
WARNING: Member name normalized: 'pkg/sub2' -> 'pkg/sub2/'
WARNING: Member name normalized: 'pkg/sub1' -> 'pkg/sub1/'
```

`archivey l linux.tar.gz` would print thousands of these. Pre-existing library
behavior (previously leaked through the last-resort handler), but D4's proper
handler makes it prominent, and "the friendliest CLI" cannot warn per-directory
on well-formed input. Library-side fix, same logic as this PR's own ZIP
ASCII-sniff fix: adding the canonical trailing slash to a DIRECTORY-typed
member is *not an observable normalization event* — suppress the diagnostic
when the only delta is the trailing slash on a directory (in `tar_reader`'s
presented name, or centrally in `emit_member_name_normalized`).

### R4 — Needs maintainer sign-off: no-index always-wrap changes a merged-spec rule

`660f7ed` rewrites the smart-dest rule for formats without a cheap index (plain
TAR and all compressed tars; future stdin): **always** extract into
`./<stem>/`, never scan-then-reuse. Verified consequence: a single-root source
tarball — the most common real-world archive —

```
$ archivey x src.tar.gz      # contains only src-1.0/…
extracting into src/
→ ./src/src-1.0/f.txt        # was ./src-1.0/… under the #110 rule
```

The merged #110 spec promised unconditional root-reuse ("single top-level
directory → extract into `.`"). The change's spec delta and design were amended
to the new rule, it errs safe, avoids a full decompress-for-metadata pass, is
tested, and the post-hoc hoist that recovers root-reuse is parked in IDEAS.md —
all coherent. But it is a **UX regression on the flagship case** and a rewrite
of an approved spec requirement, decided implementor-side; per the
pause-and-surface rule this is the maintainer's call: accept (double-wrap until
hoist lands), schedule the hoist now, or keep the #110 behavior for *seekable*
no-index archives by paying the metadata pass. Also, if accepted: the scenario
row "single top-level dir → reuses the archive's root dir" still reads as
unconditional in the delta spec — qualify it with "indexed" to remove the
internal contradiction.

## Suggested order

R1 (one-line-ish, unblocks CI) → R2 (mechanical) → R3 (library-side suppress;
turns D4 from liability to win) → R4 (maintainer decision, then a wording fix
either way).
