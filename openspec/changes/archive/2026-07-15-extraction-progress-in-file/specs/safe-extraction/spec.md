## MODIFIED Requirements

### Requirement: Progress Reporting via on_progress Callback

The system SHALL accept optional `on_progress` callbacks on both extraction APIs
and report progress with `ExtractionProgress`:

```python
@dataclass
class ExtractionProgress:
    member: ArchiveMember
    bytes_written: int
    total_bytes_estimated: int | None
    members_done: int
    members_total: int | None
    member_bytes_written: int
```

`bytes_written` is cumulative for the operation. `member_bytes_written` is the
output bytes written for the **current** member so far. `total_bytes_estimated`
is `None` when the format lacks uncompressed size information; `members_total` is
`None` when the attempted member count cannot be known without a scan. When a
free member list exists and a `members` selector is provided, totals SHALL cover
only selected members. `members_done` counts every selected member processed,
including user-filter skips and failures, so it reaches `members_total`;
selector-excluded members are invisible. Predicate selectors evaluated against an
upfront index MUST be pure functions of the member.

For a FILE member with a streamed body, the callback MAY be invoked **more than
once** as bytes are written: intra-member reports carry `member` = the current
member, `members_done` = the number of members fully completed *before* this one,
and a non-decreasing `member_bytes_written` that has not yet reached the member's
size. Each processed member SHALL additionally produce a terminal report in which
`member_bytes_written` equals the member's `size` (or, when `size` is unknown,
the final observed byte count), so a consumer can always complete a per-member
progress bar. Members without a streamed body (directories, symlinks, hardlinks)
SHALL produce a single report with `member_bytes_written == 0`. The reporting
frequency is bounded by the extraction copy chunk size; when `on_progress` is
`None`, no additional per-chunk work is performed beyond existing byte counting.

#### Scenario: progress matrix

| Case | Expected |
| --- | --- |
| `extract(..., on_progress=cb)` | `cb` called with cumulative bytes, per-member bytes, and counters |
| Large FILE member streamed | `cb` invoked multiple times with non-decreasing `member_bytes_written`, ending at the member `size` |
| FILE member smaller than one copy chunk | `cb` invoked once with `member_bytes_written == size` |
| Directory / symlink / hardlink member | Single report with `member_bytes_written == 0` |
| Member with unknown `size` (late-bound / streaming) | `member_bytes_written` still reported; terminal report equals final observed byte count |
| Format cannot provide uncompressed sizes | `total_bytes_estimated is None` |
| Free list + selector | Totals cover selected members only; filter skips/failures still advance `members_done` |
| `on_progress is None` | No callback; no extra per-chunk work beyond byte counting |
