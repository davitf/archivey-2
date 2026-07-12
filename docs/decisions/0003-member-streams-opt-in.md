# 0003 — Member streams opt-in (non-seekable, single live stream by default)

- **Status:** accepted
- **Date:** 2026-07-11
- **Provenance:** OpenSpec change `concurrent-member-streams` (proposal + design);
  PRs promoting `MemberStreams`; `docs/costs.md`

## Context

Seeking inside a compressed member and overlapping opens are format-dependent traps
(O(n) rewinds; decoder thrash on compressed TAR; solid re-decode). An unconditional
“always seekable / always concurrent” API lets developers test on ZIP and ship a footgun
on TAR/7z. Cost receipts alone are too passive (warnings deferred).

## Decision

Default `member_streams=MemberStreams(0)`:

- streams report `seekable() is False`; `seek()` → `io.UnsupportedOperation`
- at most one live member stream; a second overlapping `open()` → `ConcurrentAccessError`

Opt in with `MemberStreams.SEEKABLE` and/or `MemberStreams.CONCURRENT` at
`open_archive()`. Same rule for `open_stream(..., seekable=False)`. Seek indexes /
accelerators are **demand-driven**. Uniform across every format, including directory.

## Consequences

- Default path: no shared-handle locks, no seek tables, no accelerators.
- Strict default is reversible pre-1.0; permissive-then-gate would be a breaking change.
- Solid **open-order** cost remains the caller’s algorithm (`AccessCost` /
  `stream_members()`), not something `CONCURRENT` erases.
