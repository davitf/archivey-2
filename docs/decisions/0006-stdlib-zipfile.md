# 0006 — Stdlib `zipfile` for the ZIP core

- **Status:** accepted
- **Date:** early architecture
- **Provenance:** `docs/grab-bag/ARCHITECTURE.md` §5.1; OpenSpec `format-zip`

## Context

Alternatives (`python-libarchive-c`, etc.) offer speed or broader edge-case coverage at
the cost of native dependencies and packaging pain.

## Decision

Use stdlib `zipfile` for core ZIP read/write. Document gaps (multi-volume rejected;
some methods unsupported at read). Optional native/streaming ZIP reader remains backlog
(`IDEAS.md`).

## Consequences

- Zero-dep ZIP path; seekable sources only for this backend.
- Multi-disk / split ZIP → clear `UnsupportedFeatureError`.
