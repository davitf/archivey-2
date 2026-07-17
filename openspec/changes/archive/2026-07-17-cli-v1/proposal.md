## Why

Reading is release-complete; the missing wedge is a shell front-end that demos
safe extraction, doubles as the maintainer's inspection tool, and meets the
"iterate members and hash" audience halfway. The existing `cli` spec only covers
thin `list`/`test`/`extract` — too narrow for the jobs we need (inspect, verify,
extract with policy, detect/info, and a reserved path for hash + salvage).

## What Changes

- Implement the `archivey` command with **subcommand** verbs — each a bare word
  with a single-letter bare-word alias (`list`/`l`, `test`/`t`, `extract`/`x`,
  `info`/`i`); verbs are never dash-prefixed (no `-x` mode selectors). **Bare
  invocation defaults to `list`**.
- First-cut verbs: `list`, `test`, `extract`, `info`/`detect` (archive identity +
  format detection). Reserve `hash`, `create`, `convert` in the grammar without
  implementing them yet where noted.
- `extract` exposes `ExtractionPolicy` (`strict` / `standard` / `trusted`;
  default `strict`) and defaults overwrite to `rename` (library stays `ERROR`).
  Destination is `-d`/`--dest`; positionals after the archive are filters only —
  no bare positional dest. When `-d` is omitted the dest defaults to a smart
  enclosing directory (`./<archive-stem>/`, reusing a single archive root when
  present) to prevent tarbombs; `-d .` opts back into classic splatter-into-cwd.
- Member filters: positionals after the archive are **include** patterns;
  `--exclude` (repeatable, long-only) subtracts; no redundant `--include`.
- `list` defaults to a human layer-1 view (type, size, mtime, mode, encrypted,
  link target); stored digests opt-in so they do not pollute the default view.
- Reserve `--salvage` as a future flag on `extract` / `convert` and on read-side
  verbs (`list` / `test` / `hash`) — no behavior in this change.
- Keep CLI-only deps (`tqdm`) behind `[cli]`; parser is stdlib `argparse` so the
  base install stays zero-third-party. Core library remains importable without
  CLI deps.
- **BREAKING** (pre-release only): widens the `cli` capability contract beyond
  the current three-subcommand matrix; no published package yet, so no user
  breakage.

## Capabilities

### New Capabilities

<!-- none — extends existing `cli` -->

### Modified Capabilities

- `cli` — subcommand grammar (bare-word verbs + single-letter aliases),
  default-to-list, policy-aware extract, layered
  list output, `info`/`detect`, reserved `--salvage` / `hash` / write verbs,
  exit-code and packaging notes.
- `packaging-and-extras` — console script / `python -m archivey` ship with the
  base package; `[cli]` remains tqdm-for-progress only.

## Impact

- New package surface: `archivey.cli` (or `archivey.__main__` + console script),
  argparse (or equivalent) modules per verb — not a public library API.
- Depends on stable read/extract APIs; prefers landing after
  `cross-platform-name-safety` so `OverwritePolicy.RENAME` exists for extract.
- Tests: CLI behavior matrix (argv → stdout/stderr/exit), progress absent without
  `[cli]`, policy flags map to library enums.
- Docs: short usage section; migration/demo copy for the "safer unzip" story.
