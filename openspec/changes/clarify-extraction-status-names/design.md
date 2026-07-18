## Context

`ExtractionStatus` is a public, exported enum surfaced on every
`ExtractionResult`. After the api-coherence Q1 follow-up split non-current
duplicates into `SUPERSEDED`, the remaining generic names (`SKIPPED`, `REJECTED`)
no longer name their own reason. This change is naming + two spec/code
reconciliations; no extraction *behavior* changes except correcting the written
contract to match the code.

## Decisions

### D1 — A `filter` returning `None` yields no result (spec corrected)

`safe-extraction` states a user `filter` returning `None` produces a `SKIPPED`
result. The coordinator (`extraction.py`, `for original, stream in
reader.stream_members(...)`: `if transformed is None: pass`) appends **no**
`ExtractionResult` — the member is dropped like a selector exclusion.

Resolved (maintainer, this change): **the code is correct.** A filter returning
`None` is the caller electing to exclude a member, which is not an extraction
outcome to report. The spec scenarios and the `SKIPPED` meaning clause are
corrected to say "no result". This is what makes `NOT_OVERWRITTEN` accurate: the
only remaining producer of that status is `OverwritePolicy.SKIP` finding an
existing destination.

Rejected alternative: make the code emit a `SKIPPED`/`FILTERED` result for
filter-drops. That would keep `SKIPPED` broad (filter-drop + overwrite-skip) and
force either a vaguer name or a second status; it also reverses long-standing
behavior for no caller benefit (selector exclusions already produce no result, so
filter exclusions matching them is the least-surprising contract).

### D2 — `NOT_OVERWRITTEN` over the alternatives

Candidates for the overwrite-skip outcome: `SKIPPED` (keep), `EXISTS` /
`ALREADY_EXISTS`, `KEPT` / `PRESERVED`, `NOT_OVERWRITTEN`.

Chosen: `NOT_OVERWRITTEN`. It is self-documenting (no docstring needed to know
why), it is an outcome/past-participle like every other value (`EXTRACTED`,
`SUPERSEDED`, `BLOCKED`, `FAILED`) rather than a cause (`EXISTS`), and it mirrors
the policy that produces it (`OverwritePolicy.SKIP` → the existing entry was not
overwritten). `SKIPPED` is idiomatic in `unzip`/`rsync`, but with `SUPERSEDED`
and `BLOCKED` also being forms of "skip" it no longer disambiguates.

### D3 — `BLOCKED` over `REJECTED` / `*_BY_POLICY`

The status covers a `FilterRejectionError` from **either** a hardwired universal
safety check (path traversal, symlink escape) **or** a policy `MemberFilter`.

Chosen: `BLOCKED`. It reads as "a gate stopped this," stays accurate for both
safety and policy, and does not narrow to one mechanism. `REJECTED` does not say
who/why. `BLOCKED_BY_POLICY` / `REJECTED_BY_POLICY` is *less* accurate — a
zip-slip block is safety, not policy — so the suffix would mislabel the
universal-safety case. `BLOCKED_BY_FILTER` leaks the internal `MemberFilter`
mechanism into a public status and would rot if the block set grows.

### D4 — Cascade the rename to the paired diagnostic

`EXTRACTION_MEMBER_REJECTED` + `ExtractionOutcomeContext.status="rejected"` are
emitted for exactly the `BLOCKED` outcome, and a validator enforces the pairing
(`diagnostics.py`: `EXTRACTION_MEMBER_REJECTED requires status='rejected'`).

Resolved: **cascade.** Rename the code to `EXTRACTION_MEMBER_BLOCKED`
(`"extraction_member_blocked"`) and the context status literal to `"blocked"`, so
a caller sees one word for one event across the result status and its diagnostic.
Leaving the diagnostic as `REJECTED` would be a permanent status/diagnostic
vocabulary split for a purely cosmetic diff saving.

## Scope boundaries (intentionally unchanged)

- `OverwritePolicy.SKIP` (the *policy* value) keeps its name — the policy is still
  "skip", the *result* of applying it is `NOT_OVERWRITTEN`.
- The overwrite-resolution literal `resolution="skipped"` on the overwrite
  diagnostic (`Literal["renamed","replaced","skipped","errored"]`) is unchanged:
  it names the resolution *action*, not the result status, and reads correctly
  ("the overwrite was skipped").
- `EXTRACTION_MEMBER_FAILED` / `FAILED` are unchanged.
- No back-compat aliases: archivey is pre-1.0 and prefers an honest rename now
  over carrying a deprecated misnomer.

## Adjacent drift resolved with this change

`safe-extraction` pseudocode declares `class ExtractionStatus(str, Enum)`. The
implementation already matches (`ExtractionStatus(str, Enum)`), keeping
serialization symmetry with `HashAlgorithm` / `DiagnosticCode`.
