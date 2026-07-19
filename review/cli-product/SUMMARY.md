# CLI-as-a-product review — SUMMARY

> **Status (2026-07-19):** findings in #144; **P1–P3/P5–P7/P9–P13/D1 landed**
> (Q1/Q3 decided). Still need **Q2** (P4 `--json`) and **Q5–Q6** (P14). See
> `../STATUS.md` §1.

Deep review per `brief.md`: the merged `src/archivey/cli/` on `main` (post-#120,
post-#131 fixes), judged as a **product** — muscle memory, output, errors, exit
codes, discoverability — not re-reviewing implementation correctness.

**Baseline** (2026-07-18, `main` @ `0136024`, config `[all]`+`[cli]`, Python 3.11):
1709 passed / 131 skipped / 3 deselected; `pyrefly` 0 errors; `ty` clean; `ruff
check` clean (`ruff format --check` flags only pre-existing files under
`review/archive/`). Core-only findings reproduced in a fresh no-extras venv
(`uv venv` + `pip install -e .`); everything else reproduces in `[all]`.

## Headline

**The grammar is a success and the safety demo mostly delivers — but the
"honest damage" story collapses at the exact moment it's supposed to shine.**
Typing `archivey foo.zip` and getting an instant listing is genuinely
delightful; the smart destination, the `renamed:` notices, and the closing
`N extracted … → dir/` summary make the tarbomb protection *legible*, which is
the whole demo. The two things that would make a new user walk away are both
about damage and absence, not grammar: **one bad or hostile member aborts the
entire extraction** (a traversal entry means *zero* files extracted, under
every `--policy` — `unzip` skips it and extracts the rest), and **a filter or
positional that matches nothing succeeds silently with exit 0** — which is
precisely what the `unzip a.zip out`/`tar` muscle-memory reflex produces. Both
failure modes are silence-shaped, so they cost trust in a way a loud error
never would. Alongside those: member names can inject terminal escapes into
output (a quoting gap for a safety-branded tool), there is no `--json` for the
scripting audience VISION names, and several error paths still show
translated-exception reprs (`BadZipFile('File is not a zip file')`,
`ArchiveFormat.TAR_ZST`) instead of prose.

## First five minutes (new-user walkthrough)

Everything below is a real session (config `[all]`, cwd is an empty dir).

1. `archivey photos.zip` → aligned listing, instantly. **Delight.** The
   default-to-list gamble pays off; nothing to learn.
2. `archivey x photos.zip` → `extracting into photos/` … `4 extracted, 0
   renamed, 0 skipped → photos/`. **Delight** — the tarbomb protection
   announces itself; I never read a man page. Running it *again* gave
   `extracting into photos (1)/` — visible, sensible.
3. `archivey x photos.zip out` — my `unzip`/`7z` reflex for "extract into
   `out`". Output: `0 extracted, 0 renamed, 0 skipped → .`, **exit 0**.
   **Confusion.** Nothing told me `out` was treated as an include filter that
   matched nothing, and the success exit meant my script wouldn't notice
   either. I only found `-d` by reading `--help`. (P2)
4. `archivey x project.zip project` — tar reflex for "extract that dir".
   Same silent `0 extracted`, exit 0; fnmatch needs `project/*`. (P2)
5. `archivey x evil.tar` (one `../` member) → `Path traversal ('..') in member
   name … extraction stopped; remaining members were not extracted`, exit 1,
   and **nothing extracted at all** — `unzip` would have extracted the three
   safe files and warned. First impression: "the safe tool is the one that
   couldn't extract my archive." (P1)
6. `archivey t photos.zip` → `4 OK, 0 failed`, quiet, exit 0; with a
   deliberately flipped byte → `FAIL docs/guide.md: Digest mismatch for
   'crc32' …`, exit 1. **Trust-building** — this is the crisp verify contract.
7. `archivey secret.7z` prompted `Password:` on a TTY (no echo, worked), and
   `--password` worked pre- and post-verb. Pressing **Ctrl-D at the prompt
   dumped a 30-line traceback** (`EOFError`). (P5)
8. `archivey lsit photos.zip` (typo) → `[Errno 2] No such file or directory:
   'lsit'` — raw errno, no hint my *verb* was wrong. (P6)

Verdict: minutes 1–2 convert; minutes 3–5 are where the wedge audience leaks.

## Findings (ranked by adoption impact)

Severity: **H** would change a new user's adoption decision; **M** costs trust
or a named audience; **L** polish. Status: blocker = fix before the first
public 0.2.0; polish = after.

| # | Sev | Where | One-liner | 0.2.0 |
|---|-----|-------|-----------|-------|
| P1 | **H** | `cli/extract_cmd.py` (+ library `OnError`) | One bad/hostile member aborts the whole extraction under STOP. | **done** — CLI defaults to `CONTINUE`; `--stop-on-error`; exit 3 for policy-only blocks (Q1) |
| P2 | **H** | `cli/filters.py`, extract/list/test | Include patterns that match nothing succeed silently with exit 0. | **done** — warn per pattern; extract/test exit 1; list exit 0; `-d` hint (Q3) |
| P3 | **M** | `cli/format.py:52-85`, `cli/test_cmd.py`, `cli/extract_cmd.py` notices, tqdm desc | Member names print raw to the terminal: ANSI escapes render (demo'd red text) and `\r` rewrites the line (`line1\rOK  everything fine.txt`). A safety-branded tool should quote control bytes like `ls`/GNU `tar` do. | blocker |
| P4 | **M** | (absent) | No `--json`/`--porcelain` anywhere; the "iterate members and hash" scripting audience must parse aligned human columns with no stability promise. Deliberately deferred in `cli-v1` design (needs a member schema) — but it is the wedge gap for one of VISION's two named audiences. | Q2 (recommend: first 0.2.x) |
| P5 | **M** | `cli/password.py:19` | Ctrl-D (EOF) at the `Password:` prompt → uncaught `EOFError`, full traceback, exit 1. Catch → treat as "no password given". | blocker (small) |
| P6 | **M** | `cli/main.py:362-364` | Missing file and verb typos surface as raw errno reprs: `archivey lsit a.zip` → `[Errno 2] No such file or directory: 'lsit'`. No prose, no "did you mean a verb?" even though the signature (open failed + leftover pattern args) is detectable. | blocker (message), hint = polish |
| P7 | **M** | library messages, `cli/info_cmd.py:16` | Exception/enum reprs leak into user prose: truncated zip → `Could not open ZIP archive: BadZipFile('File is not a zip file')` (misleading *and* repr-y); suffixes like `format=ArchiveFormat.ZIP`; `info` prints `SEVEN_Z`; zstd message says `Format ArchiveFormat.TAR_ZST is not available`; zipcrypto with **no** password says "Wrong password" (#131 D8 residue). | partial (worst cases) |
| P8 | **M** | `cli/test_cmd.py:56-73,127` | Archive-wide failures under-report: missing `unrar` on a 4-member RAR → `0 OK, 1 failed` (one FAIL line for a whole-archive condition; solid-abort loses the remaining members with no "N not tested" note). The pass/fail exit contract is right; the *counts* aren't honest. | polish |
| P9 | **L** | `internal/streams/archive_stream.py:408` (library, CLI-visible) | Tripped bug: `[all]` install, `x src.tar.gz` warns "Install the 'seekable' extra (rapidgzip)" — rapidgzip **is** installed; the AUTO size-gate declined it, and the warning text doesn't know that. Misleading advice on the flagship demo path. | polish (fix text) |
| P10 | **L** | `cli/main.py` help | `--help` is clean but example-free: smart-dest, `-d .`, filter grammar, `--exclude` are invisible until the man-page moment. One epilog with 4 examples fixes it. | polish |
| P11 | **L** | `cli/extract_cmd.py:268,324-336` | Hoist/summary micro-copy: `extracting into src/` … `moved to src/` reads as a no-op; single-root reuse says `→ .` when everything actually landed in `./project/`. | polish |
| P12 | **L** | argparse positionals | `archivey x` → "the following arguments are required: archive, patterns" — `patterns` is not required. | polish |
| P13 | **L** | `cli/main.py:134-139` | Reserved-surface asymmetry: `--salvage` pre-verb is `unrecognized arguments` (globals otherwise work pre-verb); `-x`/`-l` get no "verbs are bare words — did you mean `x`?" hint. | polish |
| P14 | **L** | `cli/main.py:158-162`, `cli/info_cmd.py` | No capability/dependency view: `--version` prints only the version (design Decision 10 mentioned a dependency matrix); `info` answers "what is this file" but not "can *this install* read it / what will it cost" (no `CostReceipt` story). | Q5/Q6 |
| D1 | **L** | `cli/format.py` (from api-coherence) | List line has no mark for `ANTI` (falls to `"?"`, same as `OTHER`) and no non-current / superseded indicator — the member model's own distinctions are invisible in the first consumer. Folded here from api-coherence when that review archived. | polish |

## What is actually fine

Verified hands-on and worth defending as-is:

- **The grammar.** Bare-word verbs + single-letter aliases, default-to-list,
  known-verb-wins with the `list <path>` escape hatch — zero friction in
  practice, and `archivey foo.zip` is the ten-second demo the design hoped.
  `-x` correctly rejected as an option; reserved verbs (`hash`/`create`/
  `convert`/`cat`) give clean "not implemented yet" at exit 2; `-` gives the
  reserved-stdin message at exit 2 (#131 D7 unified — confirmed).
- **The smart destination, end to end.** Multi-top → `./photos/` announced as
  `extracting into photos/`; rerun → `photos (1)/`; single-root zip reuses
  `.`; `src.tar.gz` wrap+hoist lands `src/` correctly; `notes.txt.gz` →
  `notes.txt` in cwd; `-d .` splatters on request. Every spec row I exercised
  behaved as documented.
- **Collision visibility** (#131 F3/D2 fixed): every `renamed:`/`skipped:` is
  printed, plus the always-on closing summary — the `rename` default is now
  defensible because it's loud.
- **Stream hygiene.** Listings pipe clean (`| head` → exit 0, no noise —
  BrokenPipe fixed); data on stdout, everything else on stderr; progress
  auto-suppresses when piped; `--track-io` goes to stderr.
- **Progress.** tqdm bar under a TTY shows member name + bytes/total/%,
  leaves a 100% line, closes before the summary; absent cleanly without
  `[cli]` (core-only venv: no import, no complaint).
- **Password UX.** TTY prompt (no echo) works; non-TTY quietly declines to
  prompt; `--password` works pre- and post-verb (#131 F1 fixed).
- **Exit codes.** 0/1/2 exactly as spec'd everywhere I probed; `test` has the
  crisp contract (`3 OK, 1 failed` → 1) and quiet-by-default matches
  `unzip -t` norms.
- **Dependency honesty (core-only venv).** Command runs from the base
  install; AES 7z → "The 'cryptography' package is required for AES
  decryption (install the 'crypto' extra)"; missing `unrar` message is
  best-in-class ("Install RARLAB unrar — unrar-free / unar / 7z are not
  supported as substitutes"); RAR *listing* works without `unrar`.
- **Scale.** 10k-member listing in ~0.4 s, aligned, greppable.
- **Help hygiene.** No SUPPRESS leakage, no internal structure visible,
  aliases shown, reserved verbs labeled "reserved".

## Theme files

- `grammar-and-defaults.md` — muscle memory, filters, smart dest (P1, P2, P10–P13)
- `output.md` — human + machine output, progress, track-io (P3, P4, P8, P11, P14)
- `errors-and-exit-codes.md` — error prose, damage story, exit codes (P1, P5–P9)
- `QUESTIONS.md` — maintainer decisions (Q1–Q6)

## Provenance

#131's F1–F12/D1–D8/R1–R4 were verified fixed where this review touched them
(pre-verb globals, BrokenPipe, rename visibility, hoist, exit-code flavor,
logging handler) and are **not** re-reported. P1 is distinct from #131 D3
(which fixed `test`'s continue-on-failure): it is about `extract`, where STOP
still rules. P7 overlaps #131 D8 (wrong-password message) only for the ZIP
no-password case, which remains.
