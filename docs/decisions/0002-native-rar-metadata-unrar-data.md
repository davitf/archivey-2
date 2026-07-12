# 0002 — Native RAR metadata; system unrar for member data

- **Status:** accepted
- **Date:** 2026-07
- **Provenance:** `VISION.md`; `docs/grab-bag/ARCHITECTURE.md` §5.7; OpenSpec `format-rar`

## Context

`rarfile` couples listing to its decompressor stack and does not match Archivey’s
streaming / cost model cleanly. RAR compression is proprietary; a full native
decompressor is out of scope.

## Decision

Parse RAR3/RAR5 **metadata natively** (list without `unrar`). Decompress member **data**
via the RARLAB `unrar` binary (process boundary). Decrypt encrypted headers natively via
`[crypto]` / `[rar]`. Keep `rarfile` as a test oracle only.

## Consequences

- Listing works without `unrar`; reading compressed members requires it on `PATH`.
- Solid `stream_members()` uses one `unrar p` pipe, not one process per member.
- Refuse silent fallbacks to `unrar-free` / `unar`.
- The spec’s optional “extract-hack” (single-member temp RAR for tiny random opens) is
  **deferred** — allowed by `format-rar` but not implemented in the native reader change.
