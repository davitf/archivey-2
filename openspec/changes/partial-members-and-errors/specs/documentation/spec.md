## ADDED Requirements

### Requirement: Document complete-or-raise listing vs MemberListReport

The end-user guide (`docs/usage.md` and related Gotchas / API notes) SHALL
document the dual listing contract:

- `members()` / `scan_members()` — complete listing or raise (assert completeness).
- `members_report()` → `MemberListReport` — recovered members plus `error` when the
  archive ends in a terminal listing failure (VISION damaged-input recipe).
- `__iter__` / `stream_members` — yield recovered members then raise on the same
  failures (either access mode).

The docs SHALL state that diagnostics alone are not the primary signal for these
failures, that an incomplete pass does not publish a complete member cache, and
that RA extract-prep remains fail-closed (no partial writes from a corrupt
archive). Salvage / `--salvage` remains out of scope and separately reserved.

#### Scenario: listing honesty documentation

| Case | Expected |
| --- | --- |
| Reader wants inventory of a possibly damaged tar | Finds `members_report()` recipe (check `error`, use report `.members`) |
| Reader wants “fail if not complete” | Directed to `members()` / `scan_members()` |
| Reader looks for salvage/best-effort | Pointed to reserved/future salvage — not `members_report` |
