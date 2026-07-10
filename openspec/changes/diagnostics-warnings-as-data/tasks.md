# Tasks — diagnostics: warnings as queryable data

> Specs-only proposal. These tasks describe the implementation when the change is accepted
> and scheduled; nothing is implemented here. Several open decisions (design.md §7) should
> be settled first. Run tools through `uv` (`uv run pytest`, `uv run pyrefly check`,
> `uv run ty check`, `uv run ruff`).

## 1. The primitive

- [ ] 1.1 Add `Diagnostic` (frozen dataclass), `DiagnosticSeverity`, and a `DiagnosticCode`
      enum seeded with the current warning sites (`NAME_NORMALIZED`, `DETECTION_CONFLICT`,
      `REWIND_COST`, `MEMBER_SKIPPED`, `PASSWORD_GUESSED`, `INVALID_TIMESTAMP`,
      `DIGEST_UNVERIFIABLE`, `SCAN_ENTRY_VANISHED`, `TRAILING_DATA`).
- [ ] 1.2 A central emit path that (per policy) attaches, aggregates, calls back, logs, and
      optionally escalates — replacing raw `logger.warning` calls incrementally.

## 2. Exposure channels

- [ ] 2.1 Per-operation collection on the reader (`reader.diagnostics`) and on the
      `extract()` result; same `Diagnostic` objects referenced from the natural surface.
- [ ] 2.2 Natural-surface accessors: `ArchiveMember` (normalization/timestamp/digest),
      `FormatInfo`/`ArchiveInfo` (detection conflict, trailing data), `CostReceipt`
      (rewind), `ExtractionResult` (skip, guessed password).
- [ ] 2.3 `ArchiveyConfig.on_diagnostic` callback, invoked at emission.
- [ ] 2.4 `WarningPolicy` (default + per-code disposition IGNORE/COLLECT/RAISE) on
      `ArchiveyConfig`; `RAISE` escalates per the settled §5 decision.
- [ ] 2.5 Bounded aggregation with exact per-code counts (design.md §6); share the O1 bound.

## 3. Migration

- [ ] 3.1 Convert each of the ~17 `logger.warning` sites to emit a `Diagnostic` (which still
      logs), starting with normalization / detection / skip / guessed-password.
- [ ] 3.2 Keep the `logging` behaviour byte-for-byte for callers that only use logging.

## 4. Tests

- [ ] 4.1 Each code: emitted with correct context, attached to its surface, present in the
      collection, delivered to the callback, and (when policy=RAISE) raised.
- [ ] 4.2 Default config still only logs (no behaviour change); volume/aggregation bound
      holds under a many-normalization archive. Green in all three dependency configs.
