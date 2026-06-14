# Archivey v2 — Agent Guide

This repo (`archivey-2`) is the clean-slate **v2** of the Archivey archive
library: read, stream, and safely extract ZIP / TAR / RAR / 7z / ISO / directory
/ single-file-compressed archives behind one uniform interface.

## Where things live

- `SPEC.md`, `ARCHITECTURE.md`, `COMPARISON.md`, `PLAN.md` — the original prose
  design docs (reference). `PLAN.md` is the phased implementation roadmap.
- `openspec/specs/<capability>/spec.md` — the authoritative capability specs
  (OpenSpec format: requirements + WHEN/THEN scenarios). When specs and the prose
  docs disagree, the specs win.
- `openspec/project.md` — cross-cutting context: capability map, the phase →
  capability implementation-order table, and key strategy notes.
- `openspec/changes/<change>/` — in-flight change proposals (proposal/tasks).

## Reference repository: `archivey-dev`

`archivey-dev` is the **v1 / DEV** codebase that v2 selectively ports from and
whose `openspec/changes/` contain the native-reader explorations. It is a separate
repo and is NOT in this session's GitHub-tool scope.

**How to access it:** a plain HTTPS `git clone` works from this environment:

```bash
git clone https://github.com/davitf/archivey-dev.git /tmp/archivey-dev
```

Notes:
- The GitHub **API** (and WebFetch against `api.github.com`) is rate-limited for
  unauthenticated calls and returns `403` — do not conclude the repo is private;
  use `git clone` instead.
- Pin to a specific commit for reproducible ports. Known-good revision used while
  authoring these specs: `730275b7a755f8b5b8d08d3d4d9b267b5bdadb0d` (default
  branch HEAD; the clone carries no release tags).
- High-value paths inside it:
  - `openspec/changes/sevenzip-native-reader/` and
    `openspec/changes/rar-native-metadata-reader/` (+ `docs/*-native-reader-design.md`)
    — the native-parser designs this repo's `format-7z` / `format-rar` specs follow.
  - `src/archivey/` — the source to port (Phase 1).
  - `tests/` — the declarative test harness and fixtures.

## 7z / RAR reading strategy (native-first)

7z and RAR are read with **native** parsers, not `py7zr` / `rarfile`:
- 7z: native header parse + stdlib `lzma`/`bz2`/`zlib` for the common codecs
  (core, zero-dep). PPMd/Deflate64 via the `[7z]` extra; AES decryption via
  `[crypto]`; BCJ2 is detected and rejected. `py7zr` is kept only for 7z *writing*
  (`[7z-write]`) and as a test oracle.
- RAR: native RAR3/RAR5 metadata parser (drops `rarfile`); the external `unrar`
  binary remains the decompressor for member data. Encrypted headers are decrypted
  natively via `[crypto]`. `rarfile` is a test oracle only.

See `openspec/specs/format-7z/spec.md`, `format-rar/spec.md`,
`packaging-and-extras/spec.md`, and `testing-contract/spec.md`.

## Conventions

- Python 3.11+, zero-dependency core, sync-only API for v1.
- Tooling via `uv`: `uv sync`, `uv run mypy`, `uv run pytest`, `uv run ruff`.
  The package stays pip-installable (standard PEP 621 metadata, `hatchling`).
