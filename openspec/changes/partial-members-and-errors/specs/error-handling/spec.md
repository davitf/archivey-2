## ADDED Requirements

### Requirement: Terminal archive listing errors stay loud without hiding members

When a listing pass recovers one or more members and then hits a terminal
archive-level failure (corruption, truncation, or format EOF escalation such as
Option F TAR rejected header / strict missing trailer), the system SHALL surface
**both** the recovered prefix and the typed `ArchiveyError`. It MUST NOT use
diagnostics alone as the primary honesty channel for that failure.

Required surfaces:

| API | Contract |
| --- | --- |
| `members_report()` | Always returns `MemberListReport` with prefix in `members` and the failure in `error` |
| `__iter__` / `stream_members` (either access mode) | Yield every recovered member, then raise the same error |
| `members()` / `scan_members()` | Raise the error; MUST NOT return a partial list |

The system SHALL NOT publish a successful complete member cache for an incomplete
listing. `ResourceLimitError` from listing caps remains raise-only on these APIs
and is outside this damage-oriented requirement.

#### Scenario: partial listing honesty matrix

| Case | Expected |
| --- | --- |
| TAR rejected header after 3 members; `members_report()` | `len(members)==3`, `error` is `CorruptionError` |
| Same archive; RA `__iter__` | Yields 3 members, then raises `CorruptionError` |
| Same archive; `members()` | Raises `CorruptionError`; no list returned |
| Same archive; only `reader.diagnostics` consulted without listing API | Insufficient — callers must use report or catch after iteration |
| `ListingLimits` trip mid-list | `ResourceLimitError` raised; not soft-returned as report `error` |
