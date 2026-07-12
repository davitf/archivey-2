# 0008 — Single accelerator library (`rapidgzip`)

- **Status:** accepted
- **Date:** compression-library evaluation / known-issues work
- **Provenance:** `docs/internal/known-issues.md`; `docs/internal/library-analysis.md`;
  `[seekable]` extra

## Context

`rapidgzip` and standalone `indexed_bzip2` both ship overlapping C++ cores. Loading both
in one process caused heap corruption on macOS (dyld weak-symbol coalescing). Separately,
accelerator objects must be **closed** (not only joined) or interpreter shutdown can
SIGABRT.

## Decision

Depend only on `rapidgzip` for gzip **and** bzip2 acceleration
(`rapidgzip.IndexedBzip2File`). Wrap accelerators with close-on-finalize guards. Do not
import `indexed_bzip2`.

## Consequences

- `[seekable]` pins `rapidgzip` alone.
- Clean shutdown on Linux / Windows / macOS when guards run.
- Open upstream risk if the caller closes the source under a live accelerator stream
  (see known-issues Bug 3).
