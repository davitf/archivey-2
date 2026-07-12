# 0010 — Fail fast on non-seekable random access; no silent buffering

- **Status:** accepted
- **Date:** access-mode hardening (e.g. commit messaging around non-seekable RA)
- **Provenance:** OpenSpec `access-mode-and-cost`; related fix notes in git history

## Context

A convenience path that buffers a pipe into memory or a temp file to “make ZIP work”
hides unbounded resource use and surprises callers who thought they were streaming.

## Decision

With `streaming=False`, if the format needs seek and the source is non-seekable, **error
at open**. Do not implicitly buffer. Callers choose `streaming=True` or a seekable
source.

## Consequences

- Honest failure mode for pipes + ZIP/ISO/etc.
- Streaming mode remains the supported non-seekable path where the backend can adapt.
