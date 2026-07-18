## 1. Prerequisites

- [ ] 1.1 Confirm the api-coherence Q1 follow-up (PR #154 — `ExtractionStatus.SUPERSEDED`,
      shared last-entry-wins pass) is merged; rebase this change onto it

## 2. Enum rename (`ExtractionStatus`)

- [ ] 2.1 Rename `SKIPPED` → `NOT_OVERWRITTEN` (`"skipped"` → `"not_overwritten"`)
      and `REJECTED` → `BLOCKED` (`"rejected"` → `"blocked"`) in
      `internal/extraction_types.py`; update the member docstring comments
- [ ] 2.2 Sweep `internal/extraction.py` status emissions (overwrite/hardlink SKIP
      → `NOT_OVERWRITTEN`; rejection → `BLOCKED`); confirm filter-`None` still
      appends **no** result (D1 — already correct, add/keep the covering comment)

## 3. Diagnostic cascade (D4)

- [ ] 3.1 Rename `DiagnosticCode.EXTRACTION_MEMBER_REJECTED` → `EXTRACTION_MEMBER_BLOCKED`
      (`"extraction_member_rejected"` → `"extraction_member_blocked"`) in `diagnostics.py`
- [ ] 3.2 Change `ExtractionOutcomeContext.status` literal `"rejected"` → `"blocked"`
      and the pairing validator message/check
- [ ] 3.3 Update the emission site in `internal/extraction.py` (the
      `EXTRACTION_MEMBER_REJECTED` / `outcome="rejected"` branch)

## 4. CLI

- [ ] 4.1 Update `cli/extract_cmd.py` status branches/labels
      (`skipped:` → `not overwritten:`; `rejected` → `blocked`); keep the
      `superseded:` label from #154

## 5. Specs & docs

- [ ] 5.1 Sync main specs from this change’s deltas (`safe-extraction`,
      `diagnostics`, `format-rar`)
- [ ] 5.2 Sweep `error-handling` spec prose (`FAILED`/`REJECTED` → `FAILED`/`BLOCKED`)
- [ ] 5.3 Grep `docs/` and `openspec/specs/` for stray `SKIPPED`/`REJECTED`
      extraction references (exclude the unrelated pytest-job "SKIPPED" wording in
      `testing-contract`); `docs/api.md` autodoc needs no manual edit

## 6. Tests

- [ ] 6.1 Rename assertions across the extraction/diagnostic suites; add a case
      asserting filter-`None` produces **no** `ExtractionResult` (locks D1)
- [ ] 6.2 Assert a `BLOCKED` result pairs with `EXTRACTION_MEMBER_BLOCKED` /
      `status="blocked"`
- [ ] 6.3 `uv run --no-sync pytest` for affected tests; `ruff format` / `ruff check`;
      `uv run pyrefly check` and `uv run ty check` on touched paths

## 7. Verify

- [ ] 7.1 `openspec validate --strict clarify-extraction-status-names`
