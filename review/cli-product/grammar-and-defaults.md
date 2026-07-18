# Grammar, muscle memory, and the safe-extract defaults

Lens A of the brief: does the CLI meet the muscle memory of `unzip`/`tar`/`7z`
users, and where it diverges, is the divergence earned? All transcripts are
real sessions (config `[all]` unless noted; `$` lines are the exact argv).

## The verdict on the grammar itself: earned, and it works

The `cli-v1` decisions survive contact with hands. What I actually typed as a
new user worked on the first try:

```
$ archivey photos.zip
f-  rw-r--r--           6  2026-07-18 04:26  readme.txt
f-  rw-r--r--         800  2026-07-18 04:26  docs/guide.md
...                                              exit 0
```

- Default-to-list is a **delight**, not a surprise: the reflex command gives a
  read-only answer instantly. The 7z user's `archivey l`/`x`/`t` aliases all
  land where expected.
- `archivey -x photos.zip` → `error: unrecognized arguments: -x`, exit 2. The
  divergence from `tar -x` is deliberate and the rejection is clean — though
  hintless (P13 below).
- Reserved verbs behave: `archivey create new.zip` → `archive creation is not
  implemented yet`, exit 2. Same for `hash`, `convert`, `cat`, `--salvage`
  (post-verb), and `-` (`stdin archives are not supported yet…`). All exit 2,
  uniformly (#131 D7 confirmed done).
- The verb-named-file casualty is as small as the design claimed: with a file
  literally named `t` in cwd, `archivey t` runs the *verb* and errors asking
  for an archive; `archivey l t` lists the file. Acceptable, documented.

## The smart destination: the demo works and is legible

Every scenario row I exercised behaved as spec'd, and — crucially for the
wedge — the protection is *narrated*:

```
$ archivey x photos.zip            # 4 top-level entries
extracting into photos/
4 extracted, 0 renamed, 0 skipped → photos/     exit 0

$ archivey x photos.zip            # again: container collision
extracting into photos (1)/
4 extracted, 0 renamed, 0 skipped → photos (1)/ exit 0

$ archivey x project.zip           # single root project/ → reuse cwd
2 extracted, 0 renamed, 0 skipped → .           exit 0   (→ ./project/)

$ archivey x src.tar.gz            # no index: wrap ./src/, then hoist
extracting into src/
moved to src/
6 extracted, 0 renamed, 0 skipped → src/        exit 0

$ archivey x notes.txt.gz          # raw stream → cwd, gunzip-style
1 extracted, 0 renamed, 0 skipped → .           exit 0
```

`-d out/` is verbatim, `-d .` splatters on request, and the default
`--overwrite rename` is now loud (`renamed: out/readme.txt -> out/readme
(1).txt`, plus the summary count) — which is what makes the CLI/library
default split defensible. **Endorsed as-is.**

Two micro-copy nits (P11): the tar.gz hoist prints `extracting into src/` …
`moved to src/` — a no-op to the reader ("it moved from src/ to src/?"); and
the single-root-reuse case summarizes `→ .` when everything landed under
`./project/`. Saying `moved up to ./src/ (removed wrapper)` (or suppressing
the notice when the name doesn't change) and `→ project/` would finish the
story the messages are trying to tell.

## P2 — the one real muscle-memory trap: no-match filters are silent successes

The include-positional grammar itself is fine (it matches `unzip`/`7z`
conventions, `--exclude` reads naturally, exclude-wins is standard). The trap
is what happens when a positional matches nothing — because two extremely
common reflexes produce exactly that:

**The positional-dest reflex** (`unzip a.zip -d out` half-remembered,
`7z x a.zip -oout`, plain `unzip a.zip out`):

```
$ archivey x photos.zip out
0 extracted, 0 renamed, 0 skipped → .           exit 0
$ archivey x photos.zip out/
0 extracted, 0 renamed, 0 skipped → .           exit 0
```

Nothing on disk, exit **0**. The user meant a destination; the CLI heard an
include filter; nobody was told. A script gates on the exit code and marches
on with an empty directory.

**The tar bare-dir reflex** (`tar -xf a.tar project` extracts the tree):

```
$ archivey x project.zip project
0 extracted, 0 renamed, 0 skipped → .           exit 0
$ archivey x project.zip 'project/*'
2 extracted, 0 renamed, 0 skipped → .           exit 0
```

fnmatch semantics (`project` ≠ `project/readme.txt`) are defensible and
unzip-like — but only if the miss is *visible*. Same silence on `list`:
`archivey photos.zip '*.rs'` prints nothing, exit 0. An unquoted glob the
shell expanded against the wrong directory fails the same silent way.

Prior art is unanimous: `unzip` prints `caution: filename not matched:  out`
and exits **11**; `tar` prints `project: Not found in archive` and exits 2;
`7z` reports "No files to process". archivey is the outlier, and the outlier
behavior is silence — the most expensive kind of divergence.

**Recommendation (0.2.0):** per unmatched include pattern, print a stderr
warning (`warning: pattern matched no members: 'out'`); when *zero* members
were selected on `extract`/`test`, exit nonzero (see Q3 for which code —
unzip's dedicated 11 argues for using the reserved ≥3 space, but plain 1
also satisfies scripts). A cheap bonus hint for the dest reflex: if a sole
unmatched pattern on `extract` names an existing directory or ends in `/`,
add `(did you mean -d out?)`. That one line converts the most common failed
first command into a taught lesson.

## P1 (grammar-adjacent): `--policy` is undemoable because rejection aborts

Full analysis in `errors-and-exit-codes.md`, but it belongs in the defaults
story too: the brief asks whether the safe-extract defaults "protect a naive
user and stay un-annoying" — they protect, but the protection currently
throws away the rest of the archive. One `../escape.txt` member:

```
$ archivey x evil.tar -d evilout
Path traversal ('..') in member name: '../escape.txt' (member='../escape.txt')
extraction stopped; remaining members were not extracted
                                                 exit 1  (0 files extracted)
```

Identical outcome with `--policy standard` and `--policy trusted` (traversal
is rightly non-negotiable) — which means **no policy level demonstrates
"blocked the bad member, delivered the good ones."** The CLI already has
`rejected:` lines and a `N rejected` summary column wired up
(`extract_cmd.py:315-336`); they are unreachable because the library's
`OnError.STOP` default turns the first rejection into an exception. Decide at
Q1; the reject-and-continue shape is what makes the "safer unzip" pitch
concretely better than unzip (which skips `../` entries and continues,
extracting the 3 safe files here).

## P10 — help is clean but example-free

`--help` output is accurate, leak-free, and shows aliases and reserved verbs
(paste in `output.md`). But none of the CLI's three signature ideas — smart
dest (`-d .` to opt out), positional includes + `--exclude`, verb words — is
*shown*. A user discovers `-d` only after the P2 trap. argparse supports an
epilog; four lines would do:

```
examples:
  archivey archive.zip                  list members
  archivey x archive.zip                extract safely (into ./archive/ if needed)
  archivey x archive.zip -d out '*.py'  extract *.py into out/
  archivey t archive.zip                verify integrity
```

Add it to the top-level parser and (verb-appropriate variants) to `extract`,
where the `-d`-not-positional rule most needs advertising.

## P12 / P13 — small grammar burrs

- `archivey x` → `error: the following arguments are required: archive,
  patterns`. `patterns` is `nargs="*"` and not required; argparse's combined
  message lies. Cheap fix: give `patterns` a `metavar` like `[pattern ...]`
  or catch-and-rewrite; low priority but it's the very first error a
  flag-less experimenter sees. [`cli/main.py:120-131`]
- `--salvage` is registered only on subparsers, so pre-verb it's
  `unrecognized arguments: --salvage` (exit 2, but a different, less helpful
  flavor than the post-verb "not implemented yet"). Either register it on the
  common parent (rejecting like the others) or leave — noted for symmetry
  with the "globals work pre-verb" contract. [`cli/main.py:134-139`]
- `-x`/`-l`/`-t` rejections could hint: `verbs are bare words — try
  'archivey x ARCHIVE'`. argparse's `error()` can be overridden per-parser;
  one string check on the unrecognized token covers the three classic
  spellings. Polish, but it's the `tar` user's literal first keystroke.

## Filters: what is fine

- Positional includes + repeatable long-only `--exclude`: reads naturally,
  matches the tar/unzip/7z split, and worked in every combination I tried
  (`x photos.zip '*.py' --exclude 'setup*' -d code` → exactly `main.py`).
- `fnmatchcase` determinism (#131 nit fixed) — fine.
- One-archive-per-invocation: never chafed in practice; the shell-loop
  workaround is honest.
