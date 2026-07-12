# 0005 — Sync-only public API in v1

- **Status:** accepted (v1); async deferred
- **Date:** recorded in architecture trade-offs; explored in grab-bag `ASYNC.md`
- **Provenance:** `docs/grab-bag/ARCHITECTURE.md` §5.3; `openspec/project.md` deferrals;
  `docs/grab-bag/ASYNC.md`

## Context

Decoders (`zipfile`, `tarfile`, stdlib codecs, native parsers, `unrar` subprocess) pull
bytes synchronously. A fake-async wrapper over blocking I/O misleads callers.

## Decision

v1 API is synchronous only. Apps that must not block an event loop use
`asyncio.to_thread(...)`. A real async facade may come later; seams are discussed in
`ASYNC.md` (exploration, not a shipped decision).

## Consequences

- No dual-coloured core in v1.
- Placeholder: exact “cheap seams to land before async” checklist still in ASYNC.md —
  promote specific seams here when accepted.
