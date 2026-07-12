# 0009 — Zstd via stdlib / `backports.zstd`, not `zstandard`

- **Status:** accepted
- **Date:** 2026-07 (`zstd-stdlib-backend-migration`)
- **Provenance:** `docs/internal/library-analysis.md`; OpenSpec `packaging-and-extras`

## Context

`zstandard` (CFFI) silently short-read truncated frames in measured probes. CPython 3.14
added `compression.zstd`; `backports.zstd` mirrors that API on older Pythons.

## Decision

Use stdlib `compression.zstd` on 3.14+; `[zstd]` installs `backports.zstd` on earlier
versions. Do not pin `zstandard` or `pyzstd` in user-facing extras.

## Consequences

- Truncation raises instead of silent short reads.
- Seekable zstd (`indexed_zstd`) remains separate / backlog (accelerator coexistence
  concerns).
