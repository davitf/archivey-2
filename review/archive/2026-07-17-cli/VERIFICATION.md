> **Archive note (2026-07-17):** All actionable findings from this review were
> addressed on PR #120 before merge (including R1–R4 and the hoist H1–H3 / D4
> logging follow-ups documented in the rounds below). This file is the
> chronological verification log. See `review/README.md` archive table for the
> final outcome.

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

---

# Round 2 — verification of R1–R4 (`f201259`)

Re-verified end-to-end on `f201259` (full suite: **1691 passed, 0 failed**;
ruff/pyrefly/ty/`openspec validate --strict` all clean — R1's red CI test now
passes locally).

## Confirmed fixed

- **R1** ✓ — no password → `Password required to decrypt the 7z header`; wrong
  password → `Password(s) rejected for the 7z header` (both verified against a
  live header-encrypted 7z; the garbage-decrypt→parse-failure path is also
  mapped to rejected-header, with a new regression test).
- **R2** ✓ — `allow_abbrev=False` on every subparser incl. reserved verbs;
  `archivey x a.zip --over error` now exits 2; post-verb test added.
- **R3** ✓ — trailing-slash-only DIRECTORY normalization suppressed in
  `emit_member_name_normalized`; `archivey l <ordinary tar>` is warning-free;
  observable renames (e.g. `./pkg` → `pkg/`) still warn; unit test added.
- **R4 hoist, main paths** ✓ — single-root tar hoisted to `./root/` (wrapper
  removed); single-file tar hoisted to `./file`; multi-top tar stays wrapped;
  filtered streaming extract hoists the filtered root (D1 end-state); genuine
  pre-existing collision under `rename` → `root (1)/` with a notice; spec/
  design/IDEAS updated coherently and the indexed scenario row is now
  qualified.

## New findings — wrapper-identity collision (H1–H3)

`maybe_hoist_single_root` treats "the hoist target name is taken" uniformly via
`_collision_dest`, but when the sole root's name **equals the wrapper's own
name** the "collision" is with the wrapper itself — the directory the hoist is
about to delete. This is the most common tarball convention (`src.tar.gz`
containing `src/`). All three overwrite behaviors mishandle it (all reproduced):

| # | Sev | Repro (`src.tar` containing `src/f.txt`) | Result |
|---|-----|------------------------------------------|--------|
| **H1** | **Critical — data loss** | `archivey x src.tar --overwrite replace` | `_collision_dest` does `shutil.rmtree("src")` — that IS the wrapper holding the just-extracted data. The subsequent move fails (`No such file or directory: 'src/src'`), the run prints `2 extracted` and **exits 0**, and **no extracted files exist on disk**. A success-reporting invocation that destroys its own output. |
| H2 | Medium | default (`rename`) | Lands at `./src (1)/f.txt` instead of `./src/f.txt` — renamed away from a "collision" with a directory that was about to be removed. |
| H3 | Low | `--overwrite error` / `skip` | Keeps `./src/src/f.txt` (the exact double-nesting the hoist exists to remove) with a message implying a real conflict. |

**Fix (covers all three):** special-case `child.name == wrapper.name` *before*
`_collision_dest` — the target is the wrapper, so no collision logic applies:
rename the wrapper aside to a unique temp sibling (`src.hoist-tmp`), move the
child into place, rmdir the temp. ~10 lines in `maybe_hoist_single_root`; tests:
the three rows above.

**Design note (non-blocking, maintainer call):** on a *genuine* collision,
`--overwrite replace` rmtree's the pre-existing tree before hoisting, which is
more destructive than the indexed single-root path (extract into `.` merges,
replacing per-file). Spec's "subject to the overwrite policy" permits it, but
"replace colliding files" vs "delete whole colliding trees" is a real semantic
gap for explicit `replace` users — consider keep-wrapper for that case too, or
an explicit doc note.

---

# Round 3 — H1–H3 resolution + two answers (`741553e` on #120)

**Maintainer directives applied** (2026-07-17): never delete files (replace
only files being extracted); hoisting must produce the exact same result as
extracting directly into the destination.

## Q: do encrypted-header RARs have the R1 problem? — No

Verified against the RAR5 and RAR3 encrypted-header fixtures: no password →
`Password required to decrypt RAR headers`; wrong password → `Wrong password
for RAR5 header encryption` / `Failed to decrypt RAR3 headers (wrong
password?)` (RAR3 has no password-check value, hence the hedge — correct). All
name the header surface and distinguish required from rejected. No change
needed.

## H1–H3 fix: hoist is now a per-file merge equivalent to direct extraction

Pushed to #120 as `741553e` (per maintainer's "do the hoist fixes"):

- `_collision_dest` (whole-entry resolution incl. the H1 `rmtree`) replaced by
  `_merge_move`: directories merge into existing directories; file/symlink
  collisions resolve per file exactly like `extract_all` — `rename` derives the
  library's `name (N)` spelling (mirrors `_derive_free_name`), `replace` uses
  `os.replace` on the single colliding file, `skip` keeps the existing file
  (removing only our own just-extracted copy, which a direct extraction would
  never have written). **No `rmtree` anywhere; pre-existing files are never
  deleted.** Symlinks are moved as links and never descended into.
- A collision the policy cannot resolve without deleting data (`error`, or a
  dir-vs-file shape under `replace`/`skip`) stops the hoist, leaves the unmoved
  remainder under the wrapper, and exits 1 — the direct extraction would have
  failed on the same collision. (Deliberate residual divergence: direct
  extraction fails with a partial splatter in archive order; the hoist fails
  with the remainder safe under the wrapper. Layouts on the *failure* path are
  not bit-identical; success paths are.)
- The wrapper-identity case (`src.tar.gz` containing `src/`) is flattened in
  place — the wrapper becomes the root, no collision logic runs. All three
  H-repros now land at `./src/f.txt`, exit 0, no data touched.
- Summary counts fold hoist renames/skips in (`extra_renamed`/`extra_skipped`),
  so `3 extracted, 1 renamed …` matches the direct-extraction output.
- Spec delta + design updated to state the equivalence contract and the
  never-delete rule. 8 new tests: identity dir/file, per-policy equivalence
  battery seeded against the direct-extraction baseline, error-keeps-wrapper,
  and replace-never-rmtrees (dir-vs-file with a `precious.txt` inside).

## New find while running the CONTRIBUTING three-leg gate: D4 logging leak

The `all-lowest` leg failed **17** caplog-based warning tests across the suite:
`configure_cli_logging` set `propagate = False` and kept a process-global
handler on the `archivey` logger, which on pytest 8.3.0 (the declared floor)
blinds every later `caplog` assertion once any CLI verb test has run. Invisible
on the current-versions leg (newer pytest captures non-propagating loggers).
Fixed in the same commit: the handler is now installed via a `cli_logging`
context manager scoped to one `main()` invocation, `propagate` is untouched
(a present handler already suppresses the last-resort handler), and level is
restored on exit. Regression test asserts no handler/propagate/level residue.

**Gates on `741553e`:** `[all]` 1699 passed; `[all-lowest]` 1699 passed (was
17 failed); `[core-only]` 1386 passed + zero-dep check OK; ruff format/check,
pyrefly, ty, `openspec validate --strict cli-v1` all clean.
