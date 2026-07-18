## Why

`ExtractionStatus` now has three "not written" outcomes — `SKIPPED`,
`SUPERSEDED` (added in the api-coherence Q1 follow-up), `REJECTED` — and the two
older names no longer carry their own reason. `SKIPPED` reads as "skipped for
*some* reason" even though, post-`SUPERSEDED`, it means exactly one thing: an
existing destination was left in place under `OverwritePolicy.SKIP`. `REJECTED`
does not signal *why* an entry was blocked (a safety/policy gate). VISION's
"one uniform interface with honest signals" wants a status a caller can branch on
without reading a docstring.

While auditing the names, two spec/code drifts surfaced in the same area:

- The spec claims a user `filter` returning `None` yields a `SKIPPED` result, but
  the coordinator appends **no result** for that case (like a selector exclusion).
  The name `NOT_OVERWRITTEN` is only correct once this is reconciled — decision:
  the code is right; the spec is corrected.
- `format-rar` still says version-history rows extract as `SKIPPED`; they are
  non-current, so post-Q1 they are `SUPERSEDED`.

## What Changes

- Rename `ExtractionStatus.SKIPPED` → **`NOT_OVERWRITTEN`** (`"not_overwritten"`):
  the sole meaning is "existing destination kept under `OverwritePolicy.SKIP`".
- Rename `ExtractionStatus.REJECTED` → **`BLOCKED`** (`"blocked"`): blocked by a
  universal safety check *or* a policy filter (`FilterRejectionError`). No
  `_BY_POLICY` suffix — universal path-safety blocks are safety, not policy.
- **Cascade** the `BLOCKED` rename to its paired diagnostic:
  `DiagnosticCode.EXTRACTION_MEMBER_REJECTED` → `EXTRACTION_MEMBER_BLOCKED`
  (`"extraction_member_blocked"`) and `ExtractionOutcomeContext.status`
  `"rejected"` → `"blocked"`, so status and diagnostic share one vocabulary.
- Correct the filter-`None` contract: a `filter` returning `None` drops the
  member with **no `ExtractionResult`** (matches the coordinator), same as a
  selector exclusion.
- Correct `format-rar`: version-history rows extract as `SUPERSEDED`, not
  `SKIPPED`.
- Keep `EXTRACTED`, `SUPERSEDED`, `FAILED` unchanged.

## Capabilities

### Modified Capabilities

- `safe-extraction` — `ExtractionStatus` value names/semantics; filter-`None`
  yields no result; overwrite/hardlink SKIP results
- `diagnostics` — `EXTRACTION_MEMBER_BLOCKED` code + `status="blocked"` context
- `format-rar` — history-row extract status is `SUPERSEDED`

## Impact

- **Breaking** public API: `ExtractionStatus` members `SKIPPED`/`REJECTED` and
  `DiagnosticCode.EXTRACTION_MEMBER_REJECTED` are renamed (no back-compat aliases
  — pre-1.0, and honest-name churn is preferred over a permanent misnomer).
- Modules: `types`/`internal.extraction_types` (`ExtractionStatus`),
  `internal/extraction.py` (status emission + filter-`None` already correct),
  `diagnostics.py` (`DiagnosticCode`, `ExtractionOutcomeContext.status` literal +
  validator), `cli/extract_cmd.py` (labels).
- Docs: `docs/api.md` autodoc picks up new names; sweep any prose that names the
  old values.
- Tests: rename assertions across the extraction/diagnostic suites.
- **Prerequisite:** the api-coherence Q1 follow-up (`ExtractionStatus.SUPERSEDED`,
  the shared last-entry-wins pass — PR #154) is merged first; this change edits
  the post-#154 enum and its specs.
