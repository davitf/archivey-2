## Context

`ExtractionCoordinator` drives one forward pass over `(member, stream)` pairs and
owns progress callbacks (`safe-extraction`). Today progress is emitted once per
member:

- `_report_progress` (`extraction.py`) builds an `ExtractionProgress` and is
  called at the member boundary, right after `members_done += 1`.
- `ExtractionProgress.bytes_written` = `tracker.total_bytes` — cumulative for the
  whole call.

Member bytes are copied to disk by `_copy_to_fileobj`, a `@staticmethod` that
loops reading `_CHUNK = 1 MiB` slices and calls `tracker.count(len(chunk))` for
each. `BombTracker.count()` updates `_total_bytes` **and** `_member_bytes`
(reset in `start_member`) on every chunk — the per-member ratio guard needs the
running in-file figure. So intra-member position is already computed per chunk;
it is just never surfaced.

## Goals / Non-Goals

**Goals:**
- Expose the already-tracked in-file byte position and emit progress during a
  member's copy, so front-ends can draw per-file bars.
- Keep the change tiny and localized; reuse the existing `BombTracker` counter.
- Preserve the zero-`on_progress` fast path.

**Non-Goals:**
- Read/verify (`test`) progress — a separate seam, not through the coordinator.
- Progress for non-FILE members (no streamed bytes).
- Time-based throttling machinery (the 1 MiB chunk is a sufficient natural throttle).
- Changing ratio-guard, limits, or result semantics.

## Decisions

### 1. Reuse `BombTracker._member_bytes`; do not add a parallel counter

The tracker already maintains `_member_bytes` per chunk. Expose it as a
`member_bytes` property (mirroring `total_bytes`) and read it when emitting
progress. No new counting wrapper, no second traversal — the number is a
by-product of the bomb guard that already runs on the hot path.

**Rejected:** a dedicated progress counter / stream wrapper — redundant with the
guard that must run anyway; more hot-path work for the same number.

### 2. Emit from inside the copy loop, throttled by the copy chunk

Call the progress emit inside `_copy_to_fileobj`'s read loop, after
`tracker.count(...)`. The loop already reads in `_CHUNK = 1 MiB` slices, so this
is ≈1 callback per MiB of output: 1024 for a 1 GiB member, and exactly one for a
member smaller than a chunk (unchanged from today). No separate time throttle is
needed; if a finer/coarser cadence is ever wanted it is a local change to the
emit condition.

`_copy_to_fileobj` is currently a `@staticmethod` receiving only the tracker. It
needs `on_progress` plus the member/estimate/counter context to emit. Options:
make it an instance method, or pass a small `emit_progress` closure built by the
caller. Prefer the **closure**: it keeps `_copy_to_fileobj` a pure copy helper,
carries no coordinator state it does not need, and is trivially a no-op when
`on_progress is None`.

**Rejected:** emitting per `write()` regardless of chunk size (needs its own
throttle); a background timer thread (sync-only library; overkill).

### 3. Progress contract for intra-member reporting

- **During** a FILE member: zero or more reports with `member` = current,
  `members_done` = members fully completed *before* this one, and
  `member_bytes_written` strictly increasing toward `size`.
- **Terminal** per-member report: still fires (as today) with
  `member_bytes_written == member.size`; when `size` is unknown (`None`), it
  equals the final observed byte count. This guarantees a consumer can always
  finish the per-file bar, even if intermediate reports were coalesced.
- `bytes_written` (cumulative) now advances within a member too. It was already
  documented as "cumulative for the operation," so the value stays correct; only
  the *frequency* increases.
- Non-FILE members (dir/symlink/hardlink) have no streamed bytes and emit only
  their single boundary report, `member_bytes_written == 0`.

### 4. `member.size is None` → indeterminate bar, not an error

Late-bound / streaming members may have `size is None`. The callback still
reports `member_bytes_written`; the consumer shows an indeterminate/running-byte
bar (e.g. tqdm `total=None`). No special-casing in the coordinator beyond the
terminal-report rule in Decision 3.

### 5. Field placement / compatibility

Add `member_bytes_written: int` to `ExtractionProgress`. The library is the sole
constructor (handed to a callback), so adding a field is compatible for readers.
Place it after the existing fields; give it a default only if needed to satisfy
dataclass ordering (existing fields have no defaults, so a trailing
no-default field is fine).

## Risks / Trade-offs

- [More callbacks per operation] → Consumers that did O(work) per callback now do
  it up to ~1×/MiB. Mitigated: only when `on_progress` is set, and 1 MiB
  granularity is coarse. Document the frequency change.
- [`_copy_to_fileobj` no longer a pure staticmethod helper] → Kept pure via the
  closure (Decision 2); the coordinator owns the progress context.
- [Cumulative `bytes_written` now intra-member] → Benign per its existing
  "cumulative" documentation, but flagged for any consumer keying off
  boundary-only cadence.

## Open Questions

1. **Throttle policy:** is 1 callback/MiB acceptable as the fixed cadence, or do
   we want a coarser floor (e.g. ≥N MiB or ≥X% of `size`) to bound callbacks for
   very large members? Recommend shipping the natural 1 MiB cadence and only
   adding a floor if a consumer reports overhead.
2. **Coordinate with the `cli-v1` `test`/read-side progress seam** so both verbs
   feel consistent — tracked in `cli-v1`, not blocking here.
