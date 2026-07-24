# Design — multi-member gzip detection via rapidgzip's index

## Scope

Replace the O(file) `_has_additional_gzip_member` byte scan with a query against rapidgzip's
already-built index, for the multi-member disambiguation inside `_GzipTruncationCheckStream`.
Byte scan stays as a fallback. No public API; no format change; conservative direction unchanged
(never false-flag a valid file).

## The cost being removed

| Path | Today | Frequency |
| --- | --- | --- |
| Valid multi-member gzip, read to EOF | full scan for a further `1f 8b 08` (short-circuits at first magic, ~16 MiB apart) | every such read |
| Truncated single-member, ISIZE mismatch | scans the **whole file** (no magic to find) before raising | every such raise |

Both are on the accelerated seekable-gzip path — precisely where a user chose rapidgzip for
speed, then pays a second serial pass over the file.

## Why the index is authoritative where we use it

The disambiguation only ever needs to answer **"is this a valid multi-member file (so do not
raise)?"**. It is consulted after an ISIZE mismatch:

- **Valid ≥2-member file:** rapidgzip decoded to a clean EOS, so its index is complete → member
  count / boundaries are trustworthy → "≥2 members → do not raise." Correct.
- **Truncated single-member file:** there is no genuine second member. A partial index cannot
  invent one, so "index shows 1 member → raise." Correct.
- **Truncated *mid-second-member* file:** the ambiguous corner (see open question). Falling back
  to today's conservative "further magic ⇒ do not raise" here keeps the no-false-positive
  guarantee.

So swapping the scan for the index cannot introduce a false positive on a valid file — the only
direction that matters.

## Open question — rapidgzip API (must confirm before implementing)

rapidgzip 0.16 exposes an index for random access, but the exact accessor and granularity need
verification:

1. **Member vs. block boundaries.** `block_offsets()` / `export_index()` may enumerate *deflate
   block* offsets, not *gzip member* (stream) starts. Multi-member detection needs member starts.
   Determine whether rapidgzip marks stream boundaries (e.g. a per-block "is start of member"
   flag, a separate member table, or a derivable signal). If only deflate blocks are exposed,
   this change may be infeasible as stated — record that and keep the byte scan.
2. **Population timing / cost.** Confirm the index is fully populated after a sequential read to
   EOF (the point we query it) and that reading it forces no extra decode.
3. **Availability surface.** The accessor exists only on the live rapidgzip object; wire it so
   `_GzipTruncationCheckStream` can reach the accelerator handle (it wraps it) without leaking
   the dependency elsewhere.

If (1) fails, the fallback is the current behavior and this change becomes a no-op documented as
"rapidgzip does not expose member boundaries."

## Interaction with the deferred per-member ISIZE sum

`rapidgzip-truncation-investigation` defers replacing "further magic ⇒ accept" with an explicit
**sum of per-member ISIZE** (walk members, accept only when the sum matches `total % 2**32`).
That walk needs the same member-boundary data this change exposes. Build the sum on top of this
accessor rather than re-deriving boundaries with another scan.

## Testing

- Valid concatenated multi-member gzip (2 and 3 members), read to EOF via rapidgzip → no
  `TruncatedError`, and **no** second full-file read (assert the scan is not invoked, e.g. via a
  spy / counter).
- Truncated single-member → `TruncatedError`, decided from the index without a whole-file scan.
- Truncated mid-second-member → falls back to the conservative rule; still never false-positives
  on the valid sibling.
- Index-unavailable path (older rapidgzip / accessor absent) → falls back to the byte scan; behavior
  identical to today.
- Three dependency configs; rapidgzip-gated tests skip on core-only.
