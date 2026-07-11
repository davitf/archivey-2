# Diagnostics: warnings as lifecycle-aware data

## Why

Archivey's "no surprises" contract requires recoverable degradation and advisory events to
be queryable, not only formatted through Python logging. The current implementation has 17
`logger.warning` call sites covering changed names, detection conflicts, degraded seek
indexes, expensive rewinds, unverifiable digests, invalid metadata, directory-scan races,
TAR EOF problems, unavailable encrypted symlink targets, and continued extraction
failures. Logging alone discards the structure callers need for indexing, auditing, user
messages, and strict automation.

The first proposal attached diagnostics to static objects without accounting for when an
event can occur. That would put runtime rewind and EOF events on frozen open-time
`CostReceipt` / `ArchiveInfo` values, return extraction diagnostics "alongside" a list
without defining an API, and bound only one aggregate while per-member attachments could
still retain unbounded references. Pre-1.0 compatibility is not a constraint, so this
revision chooses one coherent long-term contract instead of preserving those shapes.

This remains a **specifications-only proposal**. Its implementation tasks are not part of
this change.

## What Changes

- Add a `diagnostics` capability with:
  - immutable `Diagnostic` values, stable `DiagnosticCode` string-enum values, severity,
    an opaque occurrence id, a human message, and a code-specific immutable, JSON-safe
    typed context;
  - immutable `DiagnosticSummary` snapshots with exact total/per-code counts and bounded
    retained occurrences;
  - `DiagnosticPolicy` dispositions `IGNORE`, `COLLECT`, and `RAISE`, resolved per code;
  - a single typed `DiagnosticRaisedError` escalation path; and
  - a fully ordered emission contract covering logging, callbacks, callback failures,
    reentrancy, and lock boundaries.
- Make diagnostics lifecycle-aware:
  - standalone detection diagnostics are final data on `FormatInfo`;
  - an `ArchiveReader` owns a cumulative lifetime collector, and `reader.diagnostics`
    returns an immutable snapshot each time;
  - reader-owned streams expose operation-filtered snapshots over the same collector;
  - `open_archive()` creates that collector before detection and transfers it intact to
    the reader, while top-level `extract()` shares one collector and initial watermark
    through detection, open, read, and extraction—no seeding, merging, or duplicated
    retention;
  - runtime rewind, seek-index degradation, scan-race, and trailing-EOF events remain in
    reader/stream operation aggregates, never in frozen `CostReceipt` or `ArchiveInfo`;
  - natural member-metadata occurrences may attach to `ArchiveMember`, but extraction
    occurrences remain in the report/reader aggregate because `ExtractionResult.status`
    and `.error` are already the complete per-result outcome; and
  - every library-retained aggregate entry and member attachment shares one
    collector-wide reference budget.
- Replace extraction's list return with a first-class `ExtractionReport` containing
  an immutable result tuple and the extraction operation's bounded diagnostic summary.
  `EXTRACTED`, `SKIPPED`, `REJECTED`, and `FAILED` are defined by outcome rather than by
  logging: intentional filter/overwrite skips are `SKIPPED`; safety-filter blocks are
  `REJECTED`; operational failures are `FAILED`; selector-excluded members have no result.
- Add `ArchiveyConfig.diagnostic_policy`,
  `ArchiveyConfig.max_retained_diagnostic_references`, and an optional
  `ArchiveyConfig.on_diagnostic` callback. The public policy is deliberately per-code
  only: severity rules, logger-specific rules, mutable global configuration, callback
  return values, and a second warnings framework are not added.
- Specify that `RAISE` always halts, including under extraction
  `OnError.CONTINUE`. For a missing archive EOF marker,
  `strict_archive_eof=True` takes precedence and raises `TruncatedError`; diagnostic
  policy still controls the event's counting/delivery before that specific error.
- Audit all 17 current warning calls into the initial stable taxonomy. No unused
  `PASSWORD_GUESSED` code is pre-reserved; the password-disambiguation change adds that
  code and context when it defines the event. The initial code-to-context mapping is a
  closed discriminated union. Extraction failure/rejection codes count results: a failed
  hardlink source affecting `N` continued results emits `N` correlated occurrences.
- Update the affected capabilities: `archive-reading`, `archive-data-model`,
  `format-detection`, `safe-extraction`, `error-handling`, `logging`,
  `access-mode-and-cost`, `compressed-streams`, `seekable-decompressor-streams`,
  `format-directory`, `format-tar`, and `format-zip`.
- Schedule implementation as the Phase 5 public-API follow-on that must land before the
  Phase 6 native 7z/RAR readers add more warning-producing paths.

## Impact

- **Public API:** new diagnostic values/policy/error, `reader.diagnostics`,
  `ArchiveStream.diagnostics`, `FormatInfo.diagnostics`,
  `ArchiveMember.diagnostics`, and `ExtractionReport`; `ArchiveReader.open()` and
  `stream_members()` expose `ArchiveStream`, while `extract()` / `extract_all()` return
  `ExtractionReport`. `ExtractionResult` has no diagnostics field.
- **Control flow:** default `COLLECT` remains non-fatal and logs warning-severity
  diagnostics. `IGNORE` suppresses retention/logging/callback delivery but not exact
  counts. `RAISE` delivers then raises `DiagnosticRaisedError` and is always-stop.
- **Memory:** one configured collector-wide budget covers every diagnostic reference the
  library retains in aggregate and object attachments. Exact counts remain unbounded
  integers; caller-held snapshots/copies are caller-owned and each snapshot is itself
  bounded.
- **Immutability:** reports, result outcome structure, and diagnostic summaries are
  immutable points in time; result members remain the archive model's documented live
  mutable, caller-read-only objects, so this is not falsely presented as a deep freeze.
- **Security/privacy:** diagnostic messages and typed contexts must never contain
  passwords, password candidates, provider return values, encryption keys, key material,
  or decrypted secret bytes.
- **Sequencing:** specs land now; the 11 implementation tasks remain unchecked and are
  scheduled before native-reader implementation.
