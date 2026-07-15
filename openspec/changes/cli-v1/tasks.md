## 1. Close blocking UX decisions (before extract implementation)

- [ ] 1.1 Record default `--overwrite` (`error` vs `rename`) in design.md Open Questions → Decisions
- [ ] 1.2 Record extract dest grammar (positional optional default `.` vs required `--dest`)
- [ ] 1.3 Record pattern syntax choice (positional / `--include` / `--` separator)
- [ ] 1.4 Record exit-code map and whether multi-archive + stdin are in v1

## 2. Packaging + entry points

- [ ] 2.1 Add `[project.scripts] archivey = …` and `archivey.__main__` for `python -m archivey`
- [ ] 2.2 Confirm `[cli]` remains tqdm-only; base install runs the command without progress

## 3. CLI scaffold

- [ ] 3.1 Create `archivey/cli/` package (`main` parser, shared formatting/filters helpers)
- [ ] 3.2 Hybrid argparse: subcommands + `-l`/`-t`/`-x`/`-i`; bare archive path → `list`
- [ ] 3.3 Global flags: `--password`, `--track-io`, `--version`, `-v`, progress hide/TTY
- [ ] 3.4 Reserve `--salvage` (fail-fast not-implemented); reject unknown `hash`/`create`/`convert` verbs without falling through to list
- [ ] 3.5 Ensure `-c` is not used for integrity check

## 4. Verbs

- [ ] 4.1 `list`: layer-1 default lines; `--digests` for stored hashes; `-v` diagnostics
- [ ] 4.2 `test`: full-read + stored-digest verification; summary + non-zero on failure
- [ ] 4.3 `extract`: map `--policy` to `ExtractionPolicy`; apply overwrite default from 1.1; patterns from 1.3
- [ ] 4.4 `info` / `detect`: format + identity summary without full member dump

## 5. Tests + docs

- [ ] 5.1 CLI behavior-matrix tests (argv → exit/stdout/stderr) covering default-list, aliases, policy, salvage reserved, no-tqdm progress
- [ ] 5.2 Short docs usage section for the four verbs + safer-extract demo
- [ ] 5.3 `openspec validate --strict cli-v1`
