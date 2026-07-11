# Tasks — lifecycle-aware diagnostics

> Specifications-only proposal. All 11 implementation tasks remain intentionally
> unchecked. Do not implement them as part of this proposal.

## 1. Diagnostic values and collector

- [x] 1.1 Add the public `DiagnosticCode`, `DiagnosticSeverity`,
      `DiagnosticDisposition`, `Diagnostic`, code-specific frozen context types,
      `DiagnosticSummary`, and `DiagnosticPolicy` values; enforce immutable mappings,
      the closed discriminated code→context mapping, deterministic JSON-safe
      serialization, opaque occurrence ids, and the prohibition on password/key material.
- [x] 1.2 Implement the lifecycle collector and immutable snapshots: exact lifetime,
      operation, and per-code counts; deterministic emission order; operation watermarks;
      and a single `max_retained_diagnostic_references` budget covering aggregate entries
      plus every library-retained member attachment. Create the prospective collector
      before `open_archive()` detection and transfer it to the reader without copying;
      carry one top-level `extract()` collector/watermark through detect/open/extract.
- [x] 1.3 Add `ArchiveyConfig.diagnostic_policy`,
      `max_retained_diagnostic_references`, and `on_diagnostic`; implement the complete
      IGNORE/COLLECT/RAISE matrix and ordered count → retain/attach → log → callback →
      escalation path, with no internal lock held during logging/callbacks and explicit
      same-emitter reentrancy rejection.

## 2. Lifecycle-aware public surfaces

- [x] 2.1 Add cumulative `ArchiveReader.diagnostics` and operation-filtered
      `ArchiveStream.diagnostics` snapshots over the same collector; change public
      `open()` / `stream_members()` return types to `ArchiveStream`; transfer
      automatic-detection collector ownership to the reader; and keep runtime
      seek/index/scan/EOF events off `CostReceipt` and `ArchiveInfo`.
- [x] 2.2 Add bounded `FormatInfo.diagnostics` and `ArchiveMember.diagnostics`
      projections, correlated by occurrence id without object-identity guarantees and
      attached only when the shared collector budget has a slot. Do not add
      `ExtractionResult.diagnostics`.
- [x] 2.3 Add frozen `ExtractionReport(results, diagnostics)` and frozen result outcome
      structures, while documenting that referenced `ArchiveMember`s remain live; change
      `archivey.extract()` / `ArchiveReader.extract_all()` to return the report; make
      selected user-filter and overwrite skips `SKIPPED`, safety-filter blocks
      `REJECTED`, other continued member errors `FAILED`, and selector-excluded members
      absent.

## 3. Errors and warning migration

- [x] 3.1 Add `DiagnosticRaisedError` to the public error hierarchy, carrying the
      escalated diagnostic; make it always-stop despite `OnError.CONTINUE`, and implement
      `strict_archive_eof=True` precedence so a missing EOF marker raises
      `TruncatedError` after policy delivery rather than `DiagnosticRaisedError`.
- [x] 3.2 Replace all 17 current `logger.warning` calls with central diagnostic emission
      using the complete initial taxonomy in `design.md`; preserve logger placement and
      warning severity as projections while removing direct warning-only sources of
      truth. Emit one extraction occurrence per continued result, including one correlated
      occurrence per hardlink result affected by a shared source failure.
      (Bidi-control advisory remains a non-taxonomy `logger.warning`.)

## 4. Verification and documentation

- [x] 4.1 Add focused tests for every policy-matrix cell, exact counts after retention
      exhaustion, aggregate/attachment shared-budget accounting, occurrence-id value
      correlation, immutable/JSON-safe context, secret redaction, callback order/failure,
      snapshot access from callbacks, operational reentrancy rejection, and lock release.
- [x] 4.2 Add behavior tests for detection→reader collector transfer, one-shot collector
      handoff, cumulative reader and filtered stream lifecycles, each of the 17 migrated
      warning sites, extraction group-failure count units, `ExtractionReport` scope/status
      and live-member semantics, `RAISE` versus `OnError.CONTINUE`, and all
      `strict_archive_eof` × disposition precedence combinations.
- [x] 4.3 Export and document the final public API, update examples/type contracts, and
      run lint, Pyrefly, ty, docs, and the full current/lowest/core-only test matrix before
      implementation is committed.
      (Public exports + docs/api + threat-model C2 updated; three-config matrix run on
      this branch.)
