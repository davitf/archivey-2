## 1. Close remaining parser UX decisions

- [x] 1.1 Default `--overwrite` = `rename` (CLI); library stays `ERROR` — recorded in design
- [x] 1.2 Extract dest = `-d`/`--dest`; positionals after archive = filters only. Default (no `-d`) = smart enclosing dir (`./<stem>/`, reuse single archive root, single-file → cwd) to avoid tarbombs; `-d .` = classic splatter
- [x] 1.3 Filters: positionals = include; add `--exclude` (repeatable, long-only); no `--include` (redundant); exclude wins over include. `--include-from`/`--exclude-from` reserved for later
- [x] 1.4 Exit codes = `0`/`1`/`2` argparse-aligned, `≥3` reserved (Decision 12); one archive per invocation (Decision 13); stdin deferred, `-` reserved (Decision 15)
- [x] 1.5 `test` verbosity: quiet + summary by default, `-v` per-member (Decision 14)
- [x] 1.6 Default-to-list dispatch = known-verb-wins; escape hatch `list <path>` for verb-named files (Decision 1)
- [x] 1.7 `--password` prompts on TTY when none supplied; output hygiene stdout=data/stderr=noise (Decisions 16–17)
- [ ] 1.8 **Maintainer call:** does the library expose an I/O-accounting hook for `--track-io`? If not, drop it from v1 — no `builtins.open` monkeypatch (Decision 10 / Open A)

## 2. Packaging + entry points

- [ ] 2.1 Add `[project.scripts] archivey = …` and `archivey.__main__` for `python -m archivey`
- [ ] 2.2 Confirm `[cli]` remains tqdm-only; base install runs the command without progress

## 3. CLI scaffold

- [ ] 3.1 Create `archivey/cli/` package (`main` parser, shared formatting/filters helpers)
- [ ] 3.2 argparse subparsers with bare-word verbs + single-letter aliases (`add_parser("extract", aliases=["x"])` etc.); known-verb-wins dispatch (bare archive path → `list`, reserved verbs still shadow same-named files); reject dash-prefixed verb forms (`-x`) and `-`/stdin with clear errors
- [ ] 3.3 Global flags: `--password` (prompt on TTY when unset), `--version`, `-v`, progress hide/TTY; `--track-io` only if backed by a library hook (task 1.8)
- [ ] 3.4 Reserve `--salvage` (fail-fast not-implemented); reject unknown `hash`/`create`/`convert` verbs without falling through to list
- [ ] 3.5 Ensure the verb letter `c` is not used for integrity check (reserve for future `create`)
- [ ] 3.6 Exit-code mapping (`0`/`1`/`2`); output hygiene (data→stdout, progress/summaries/prompts→stderr); lazy `tqdm` import so `core-only` never imports it

## 4. Verbs

- [ ] 4.1 `list`: layer-1 default lines; `--digests` for stored hashes; `-v` diagnostics
- [ ] 4.2 `test`: full-read + stored-digest verification; summary + non-zero on failure
- [ ] 4.3 `extract`: map `--policy` to `ExtractionPolicy`; apply overwrite default from 1.1; smart default dest from 1.2 (compute enclosing dir / detect single archive root); patterns from 1.3
- [ ] 4.4 `info` / `detect`: format + identity summary without full member dump

## 5. Tests + docs

- [ ] 5.1 CLI behavior-matrix tests (argv → exit/stdout/stderr) covering default-list dispatch (incl. verb-named file), aliases, dash-verb + stdin rejection, policy, `--exclude`, exit codes, quiet-vs-`-v` test, salvage reserved, no-tqdm progress
- [ ] 5.2 Short docs usage section for the four verbs + safer-extract demo
- [ ] 5.3 `openspec validate --strict cli-v1`
