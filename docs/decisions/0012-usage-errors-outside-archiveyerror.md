# 0012 — Usage errors are outside `ArchiveyError`

- **Status:** accepted
- **Date:** 2026-07-11 (`concurrent-member-streams`)
- **Provenance:** that change’s design D4; OpenSpec `error-handling` /
  `archive-reading`

## Context

`except ArchiveyError` is the natural “archive or environment failed” handler.
Caller bugs (undeclared concurrent open, operating on a closed reader) must not be
swallowed by that blanket.

## Decision

Introduce `ArchiveyUsageError` (and `ConcurrentAccessError`) **not** subclassing
`ArchiveyError`. Archive/mode/feature limitations stay `ArchiveyError`
(`UnsupportedOperationError`, etc.). Stream protocol stays stdlib-shaped
(`ValueError` / `io.UnsupportedOperation`).

## Consequences

- Misuse fails loudly in development.
- Applications can catch archive failures without masking programmer errors.
