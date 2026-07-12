# 0004 — Keep `streaming: bool`; drop the `Intent` enum

- **Status:** accepted
- **Date:** during v2 build (post-COMPARISON recommendation)
- **Provenance:** `docs/grab-bag/COMPARISON.md` decision update; OpenSpec
  `access-mode-and-cost`

## Context

Clean-slate design and DEV both explored an `Intent` enum (`AUTO` / `SEQUENTIAL` /
`RANDOM`). COMPARISON originally recommended adopting it.

## Decision

Keep **`streaming: bool`** (`False` = random access, fail fast if seek required;
`True` = forward-only single pass). Drop `Intent`. Eager seek-point building may return
later as an explicit opt-in; it is not `AUTO` magic.

## Consequences

- Two real modes, not three labels for two behaviors (`AUTO` did not auto-select).
- Cost receipt + declared `MemberStreams` cover performance intent separately.
