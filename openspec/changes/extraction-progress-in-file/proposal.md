## Why

`on_progress` fires **once per member**, after the member is fully written
(`ExtractionCoordinator._report_progress`, called at the member boundary), and
`ExtractionProgress.bytes_written` is the cumulative operation total. So a
front-end (the `cli-v1` progress bar, or any library consumer) cannot draw an
*in-file* bar: extracting one large member shows a frozen bar that jumps by the
whole member size in a single step at completion.

The data to do better already exists. `BombTracker` — the decompression-ratio
guard — is fed **per 1 MiB copy chunk** and already maintains both a cumulative
total (`_total_bytes`) and the current member's output so far (`_member_bytes`),
because the per-member ratio check needs the running in-file figure. The
intra-member byte position is measured on every chunk; it simply never leaves the
tracker. This change exposes it and emits progress from inside the copy loop.

## What Changes

- Add `ExtractionProgress.member_bytes_written: int` — the current member's output
  bytes so far (denominator for a per-file bar is the member's `size`, already on
  `ArchiveMember`).
- Emit `on_progress` **during** a FILE member's copy, not only at the member
  boundary, sourced from `BombTracker`'s existing per-chunk `_member_bytes`
  (exposed via a new `member_bytes` property). Throttled by the existing 1 MiB
  copy chunk (≈1 callback/MiB); small members keep a single callback as today.
- Clarify the progress contract: intra-member reports carry `members_done` =
  members *completed* (excluding the current one) and `member_bytes_written <
  size`; a terminal report per member SHALL still fire with `member_bytes_written
  == size` (or the final byte count when `size` is unknown) so consumers can
  complete the bar. Directories, links, and hardlinks keep their single
  boundary report (no streamed bytes).
- Preserve the zero-cost path: when `on_progress is None`, no per-chunk work
  beyond the byte counting the `BombTracker` already does.

- **BREAKING** (pre-release only): widens the `on_progress` contract — the
  callback may now be invoked multiple times per member, and `bytes_written`
  advances *within* a member rather than only at boundaries. A new field is added
  to the `ExtractionProgress` dataclass. No published package yet, so no user
  breakage.

## Capabilities

### New Capabilities

<!-- none — extends existing `safe-extraction` -->

### Modified Capabilities

- `safe-extraction` — `on_progress` may fire multiple times per FILE member;
  `ExtractionProgress` gains `member_bytes_written`; the per-member terminal
  report and `members_done` semantics are clarified for intra-member reporting.

## Impact

- `src/archivey/internal/extraction_types.py` — new `ExtractionProgress` field.
- `src/archivey/internal/extraction.py` — `BombTracker.member_bytes` property;
  `_copy_to_fileobj` gains a throttled progress emit (needs `on_progress` +
  member/counter context, currently a `@staticmethod` taking only the tracker).
- `src/archivey/__init__.py` — no export change (`ExtractionProgress` already
  public); docstring/field docs updated.
- Enables the `cli-v1` extract progress bar to show in-file progress; `cli-v1`
  depends on this landing for that UX (falls back to per-member bars without it).
- Tests: `on_progress` fires more than once for a large FILE member with
  monotonically non-decreasing `member_bytes_written` ending at `size`; single
  callback for a sub-chunk member; no extra callbacks for dirs/links; unchanged
  behavior when `on_progress is None`.
- Read-side `test`/verify progress is a **separate seam** (it does not go through
  `ExtractionCoordinator`/`BombTracker`) and is out of scope here.
