# Brief — The `archivey` CLI as a product (UX / ergonomics)

> **Status (2026-07-19):** findings delivered in **#144**. Unambiguous fixes
> (P3/P5–P7/P9–P13/D1) + **Q1/P1** + **Q3/P2** + **Q5–Q6/P14** implemented;
> **Q2/P4** deferred (`hash`/schema). Still open: **P8**. Triage: `../STATUS.md`.

Read `review/README.md` (conventions, VISION tie-breakers, deliverable shape). This
is a **product** review — the CLI seen through a user's eyes — **not** a code-correctness
pass. PR #131 already reviewed the implementation for bugs (F1–F12 fixed).

## Start condition

**Wait for PR #120 to merge** before running this (it was open and `unstable` when
commissioned). Review the merged `src/archivey/cli/` on `main`, plus the `cli-v1`
spec and design (`openspec/changes/cli-v1/proposal.md` / `design.md` / `specs/`),
and `docs/usage`.

## Why now

The CLI is VISION's **adoption wedge**: it "demos safe extraction, doubles as the
maintainer's inspection tool, and meets the 'iterate members and hash' audience
halfway." A wedge lives or dies on ergonomics — a correct CLI that feels foreign or
surprising doesn't convert anyone. That's a different lens from #131's bug hunt, and
no one has applied it yet.

## The grammar as shipped (from the proposal — verify against merged code)

Subcommand verbs `list`/`l`, `test`/`t`, `extract`/`x`, `info`/`i`/`detect`; bare
invocation defaults to `list`; reserved `hash`/`create`/`convert` and `--salvage`
and stdin `-`. `extract` defaults overwrite to `rename` (library default stays
`ERROR`), `--policy strict|standard|trusted` (default `strict`), `-d`/`--dest` with a
smart enclosing-dir default (`./<stem>/`) to prevent tarbombs; positionals after the
archive are include filters, `--exclude` repeatable. `list` defaults to a human view;
digests opt-in. `--track-io` surfaces measurement. `[cli]` is tqdm-only; parser is
stdlib `argparse`.

## What to evaluate (ranked by adoption impact)

### A. Does it meet muscle memory? (the biggest adoption lever)
Users arrive from `unzip`, `tar`, `7z`, `unrar`. Where archivey diverges, is the
divergence *earned* (safer/clearer) or gratuitous friction?
- Verb-not-flag mode selection (`x` vs `tar -x`), bare-word aliases, default-to-`list`
  — judged against what a `tar`/`7z` user expects to happen when they type the
  reflex command. Does `archivey foo.zip` doing a listing surprise or delight?
- The **safe-extract defaults** are the whole demo: overwrite=`rename`, enclosing-dir
  default, `--policy strict`. Do they protect a naive user *and* stay un-annoying for
  the expert (`-d .` escape hatch discoverable)? Is the tarbomb protection legible in
  the output ("extracted into ./archive/") or silent-and-confusing?
- Filters as positionals (include) + `--exclude` — does this read naturally, and does
  it collide with how `tar`/`unzip` treat trailing paths?

### B. Output — human and machine
VISION names a "iterate members and hash" (scripting) audience alongside humans.
- The default `list` view (type/size/mtime/mode/encrypted/link) — scannable? aligned?
  sensible with 10k members? Does it degrade gracefully on a non-TTY / when piped?
- **Is there a machine-readable mode** (`--json`/`--porcelain`) for the scripting
  audience, or must they parse columns? If absent, that's a wedge gap worth naming.
- `info`/`detect` output — does it answer "what is this file, can I read it, what will
  it cost" (format, confidence, encryption, the `CostReceipt` story) in a way that
  matches the library's honest-cost claim?
- `--track-io` / measurement output — useful and interpretable, or raw counters?

### C. Errors, exit codes, and the honest-damage story (VISION #3)
- **Exit codes**: are they conventional and stable (0 ok / distinct nonzero for
  not-found, bad password, corruption, partial, usage error) so scripts can branch?
  `test` especially needs a crisp pass/fail contract. Ctrl-C→130, BrokenPipe handling
  (#131 touched these — confirm they read right to a user).
- **Error messages**: does a wrong password, a truncated archive, a missing optional
  dependency (`[7z]`/`[crypto]`/`unrar` absent), or an unsupported feature produce a
  message a *human* can act on — or a translated-exception repr? The library has a
  rich `ArchiveyError` tree; does the CLI turn it into good prose?
- Partial extraction: when some members fail, what does the user see and what's the
  exit code? This is the founding "recoverable members + honest error" claim at the
  shell.

### D. Discoverability & help
- `--help` per verb: complete, accurate, example-bearing? Does `argparse`
  (`allow_abbrev=False`, `SUPPRESS` parents per #131) produce clean help or leak
  internal structure?
- Are the **reserved** verbs/flags (`hash`, `create`, `convert`, `--salvage`) handled
  gracefully (clear "not yet" with exit 2) rather than cryptic argparse errors?
- Progress (tqdm behind `[cli]`): does it render for fast *and* slow extracts, stay
  out of the way when piped, and never corrupt machine output?

### E. Consistency with the library
- Do CLI `--policy`/`--overwrite` names and semantics match the library's
  `ExtractionPolicy`/`OverwritePolicy` (so docs transfer), and is the CLI's *different*
  default (`rename` vs `ERROR`) documented and defensible?
- Does the CLI stay honest about optional deps — base install works, `[cli]` only
  adds tqdm, and a missing backend degrades to a clear message not a stack trace?

## Non-goals
- Not re-reviewing implementation correctness (#131 did; note a real bug if you trip
  one, but the deliverable is UX judgement).
- Don't design `hash`/`create`/`convert`/`--salvage` behaviour — they're reserved on
  purpose. Assessing that the *reservation* is handled well **is** in scope.
- No new third-party CLI deps beyond `[cli]`/tqdm; `argparse` is a settled decision.

## Deliverable
Per README. Suggested theme files: `grammar-and-defaults.md`, `output.md`
(human + machine), `errors-and-exit-codes.md`. Ground every UX claim in an actual
invocation and its real output (paste the session). A short "first-five-minutes"
walkthrough as a new user — where you got confused or delighted — is worth more than
an abstract critique. Rank by adoption impact, and separate "0.2.0 blocker" from
"polish later."
