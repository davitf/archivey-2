## Why

Reading is release-complete; the missing wedge is a shell front-end that demos
safe extraction, doubles as the maintainer's inspection tool, and meets the
"iterate members and hash" audience halfway. The existing `cli` spec only covers
thin `list`/`test`/`extract` — too narrow for the jobs we need (inspect, verify,
extract with policy, detect/info, and a reserved path for hash + salvage).

## What Changes

- Implement the `archivey` command as a **hybrid** CLI: subcommands plus short
  flag aliases; **bare invocation defaults to `list`**.
- First-cut verbs: `list`, `test`, `extract`, `info`/`detect` (archive identity +
  format detection). Reserve `hash`, `create`, `convert` in the grammar without
  implementing them yet where noted.
- `extract` exposes `ExtractionPolicy` (`strict` / `standard` / `trusted`) but
  not the full library knob surface; overwrite/default-policy values stay open
  until decided (see design).
- `list` defaults to a human layer-1 view (type, size, mtime, mode, encrypted,
  link target); stored digests opt-in so they do not pollute the default view.
- Reserve `--salvage` as a future flag on `extract` / `convert` and on read-side
  verbs (`list` / `test` / `hash`) — no behavior in this change.
- Keep CLI-only deps (`tqdm`) behind `[cli]`; core library remains importable
  without them. Exact install/entry-point packaging decision recorded in design.
- **BREAKING** (pre-release only): widens the `cli` capability contract beyond
  the current three-subcommand matrix; no published package yet, so no user
  breakage.

## Capabilities

### New Capabilities

<!-- none — extends existing `cli` -->

### Modified Capabilities

- `cli` — hybrid command shape, default-to-list, policy-aware extract, layered
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
