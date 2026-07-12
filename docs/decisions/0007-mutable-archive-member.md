# 0007 — Mutable `ArchiveMember` filled in place

- **Status:** accepted (reversed from an earlier frozen draft)
- **Date:** Phase 5 / data-model work
- **Provenance:** `docs/grab-bag/ARCHITECTURE.md` §2.1 / §5.2; OpenSpec
  `archive-data-model`

## Context

Some fields are unknown until member data is read (final size/CRC for gzip or ZIP data
descriptors; link targets stored in data). Under `streaming=True` the library cannot
re-materialize and hand back a new object.

## Decision

`ArchiveMember` is a **mutable** stdlib dataclass. The library may fill late fields in
place. Callers treat members as read-only and use `member.replace(**kwargs)` for edits.
Members are unhashable — key by `name` / `member_id`.

## Consequences

- Streaming pass can complete metadata without a second fetch.
- No `set`/`dict` keying on member objects.
