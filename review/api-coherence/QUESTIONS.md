# QUESTIONS — maintainer decisions

Per the pause-and-ask rule (`CLAUDE.md`, `CONTRIBUTING.md`): discrepancies and
judgement calls surfaced, not silently resolved. Ordered by weight.

## Q1 — Duplicate-name members: unify `is_current`, and what do specs mean? (P1)

Two spec artifacts disagree with each other and with the implementation:

- `safe-extraction/spec.md:230`: "Content superseded by later same-name or anti →
  `SKIPPED` on extract" — **no format qualifier**.
- `archive-data-model/spec.md:206`: the same statement scoped "(7z)".
- Implementation: ZIP/TAR never compute `is_current`; a duplicate-name ZIP/TAR fails
  default extraction with `ExtractionError` (O2 collision under `OverwritePolicy.ERROR`)
  — repro in `parity.md`.

Decision needed: **(a)** compute last-entry-wins `is_current` in all random-access
materializations and route exact-same-name duplicates through the non-current skip
(recommended — makes `tar -rf` output extract by default, restores uniformity;
detailed plan in `members-scope.md` §"What actually needs fixing"), or **(b)** declare
`is_current` a 7z/RAR-only concept in `archive-data-model`, fix the `safe-extraction`
scenario wording, and accept that duplicate-name ZIP/TAR needs a non-default
`OverwritePolicy` (then also fix `get()`'s docstring parenthetical, `reader.py:114`).
Either way the conformance sweep should assert the chosen contract instead of
special-casing duplicates (`test_corpus_sweep.py:203`).

## Q2 — `members()` scope: include non-current by default? (maintainer's added question)

Full analysis in `members-scope.md`. Recommendation: keep "everything" as the only
listing behavior, no include/exclude argument (streaming-TAR can't honor it, a
boolean doesn't carve the space — anti items are `is_current=True` — and bookkeeping
alignment breaks); invest in Q1 + docs + predicate recipes instead. Needs an explicit
yes/no so the visibility table in `safe-extraction` can be marked settled.

## Q3 — RAR `listing_cost`: `INDEXED` or `REQUIRES_SCANNING`? (P2)

`cost.py:24-27` names "a RAR with no quick-open record" as the canonical
`REQUIRES_SCANNING` example; `rar_reader.py:773` always reports `INDEXED`; the
`access-mode-and-cost` spec's example matrix is silent on RAR. Decide which is the
honest receipt (is the axis "what the format's layout requires" or "what a caller
pays after open?") and fix the losing artifact + add the RAR row to
`test_cost_receipt.py`. If the axis is layout-based, distinguishing quick-open RAR5
from plain requires plumbing a parser fact into the receipt — small but real work,
which may itself inform the choice.

## Q4 — Approve the surface changes (S1/S3)

Pre-release, all free (`surface.md`): demote the 13 `*Context` classes +
`RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE` from `__all__`; export `PasswordInput` (and
decide `OnDiagnostic`); collapse `MemberSelectorArg` into public `MemberSelector`;
drop `source_name` from `core.__all__`; fill the `api.md` gaps (`open_stream` at
minimum). Blanket approval or line-item veto?

## Q5 — A `verify` primitive (E2): now or post-0.2.0?

`archivey test` proves the gap (60 lines, generator-semantics traps, poisoned-stream
data loss). Adding `reader.verify(...) -> VerifyReport` (or `stream_members`
error-recovery) is additive — it can land after 0.2.0 without breakage, so this is a
prioritization question, not a freeze question. Related sub-decision: if it lands,
`ExtractionProgress` gets a neutral-named alias (`ergonomics.md` nits).

## Q6 — Small freeze-list confirmations

- **`WriteError`**: exported but unraisable until Phase 9. Keep (confident in the
  name) or demote until writing lands?
- **`ExtractionStatus.SKIPPED` split (E3)**: add a distinct status or a `reason`
  field on `ExtractionResult` for non-current skips? Cheap now, negotiation later.
- **`hashes` value convention**: keep `int` crc32 / `bytes` others (documented), or
  normalize to `bytes` pre-freeze? Recommendation: keep + document the convention in
  the field docstring.
- **`ArchiveFormat` display name** (S2): add `display_name()`/`label` so the CLI
  stops parsing `repr()`. Name preference?
