# Agent Guide

For repo orientation, specs, and workflow, see `CLAUDE.md` and `CONTRIBUTING.md`
(coding/testing standards, the "Before pushing…" three-config test rule).

## Cursor Cloud specific instructions

This repo is a **pure Python library** (`archivey`) — there is no server, web UI, or
runnable CLI (the `archivey` command in `openspec/specs/cli/spec.md` is planned, not
implemented). "Running the application" means exercising the library API in Python:
`archivey.open_archive(path)` / `archivey.extract(path, dest)` plus the detection
helpers (`detect_format`, `list_supported_formats`). Implemented backends are ZIP, TAR,
ISO, directory, and single-file-compressed (gz/bz2/xz/lzip/zstd/lz4/.Z); **7z and RAR
readers are not implemented yet** despite their specs/extras existing.

Environment is managed by `uv` (Python 3.11, pinned in `.python-version`). The startup
update script runs `uv sync --group dev --extra all`, so the everyday dev env is already
in place. Run tools with `--no-sync` to avoid a redundant re-resolve, e.g.:

- Tests: `uv run --no-sync pytest`
- Lint: `uv run --no-sync ruff check`  (note: `ruff format --check` currently reports
  pre-existing drift in `tests/`; that is unrelated to any single change)
- Type-check: `uv run --no-sync pyrefly check` and `uv run --no-sync ty check`
  (both must stay clean; mypy/pyright are intentionally not used)

Non-obvious gotchas:

- The startup update script also installs the system `unrar` binary and the `openspec`
  CLI (in addition to `uv sync`), so both are present without manual steps.
- **`unrar`** (system binary, from the `multiverse` apt component) backs RAR *data*
  tests; without it they skip cleanly rather than fail.
- **`openspec` CLI** lives at `~/.local/bin` (on `PATH`). `CLAUDE.md`'s
  `npm install -g @fission-ai/openspec` fails with `EACCES` here because the global npm
  prefix is not user-writable — the update script instead installs it into a writable,
  already-on-`PATH` prefix: `npm install -g --prefix "$HOME/.local" @fission-ai/openspec`.
- The full push gate runs the suite in **three dependency configs** (`[all]`,
  `[all-lowest]`, `[core-only]`); the exact commands are in `CONTRIBUTING.md`. After a
  `--no-dev` / lowest-resolution leg, restore the everyday env with
  `uv sync --group dev --extra all`.
- Docs (optional): `uv run --group docs mkdocs build --strict`.
