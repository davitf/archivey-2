## ADDED Requirements

### Requirement: Bound 7z header count fields before allocation

The system SHALL bound count fields read from the 7z header (including
`num_files` and other pre-allocated tables) against the already size-bounded,
CRC-checked header buffer before allocating per-entry structures. A count that
cannot fit in the remaining header semantics SHALL raise `CorruptionError`
(hostile/nonsensical header), independent of `ListingLimits`.

Spine `ListingLimits` (`archive-reading`) still apply when members are
registered into a materialized list and raise `ResourceLimitError` when
configured caps are exceeded. Parser bounds are defense-in-depth against
allocation before Python `ArchiveMember` objects exist; they MUST NOT be
implemented by reusing `ExtractionLimits.max_entries`.

#### Scenario: 7z header bound matrix

| Case | Expected |
| --- | --- |
| `num_files` greater than header buffer size | `CorruptionError` at parse; no giant pre-allocation |
| Legitimate archive whose header is large enough for its file count | Parse succeeds; listing still subject to `ListingLimits` |
| Archive within parser bounds but over `listing_limits.max_members` | Parse may succeed; `members()` / materialization raises `ResourceLimitError` |
