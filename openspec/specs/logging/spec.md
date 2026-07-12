# Logging

## Purpose

Standard-library logging projection for Archivey events. The library emits records
through the `archivey` logger hierarchy without configuring handlers, levels,
filters, or formatters; applications own output policy.

## Related specs

| Spec | Relationship |
| --- | --- |
| `diagnostics` | WARNING logs are ordered projections of diagnostics |
| `format-detection` | Detection conflict advisory events |
| `archive-reading` | Reader/stream diagnostic surfaces that may also log |
| `safe-extraction` | Extraction outcomes and filter decisions |
| `reader-concurrency` | Handlers run without Archivey locks |

## Requirements

### Requirement: Logging under the archivey logger hierarchy

The system SHALL emit all log messages via `logging.getLogger("archivey")` and its
named children. It MUST NOT configure handlers, levels, filters, or formatters.

| Logger | Events |
| --- | --- |
| `archivey.detection` | Format detection events |
| `archivey.normalization` | Path normalization changes, including warnings when `name` differs from `raw_name` |
| `archivey.extraction` | Extraction events and filter decisions |
| `archivey.backends.*` | Backend-specific debug messages |

#### Scenario: logger-hierarchy matrix

| Case | Expected |
| --- | --- |
| Application configures no handlers on `archivey` or ancestors | No output by default; library installs no handler |
| Magic bytes conflict with extension | `logging.WARNING` on `archivey.detection` |
| Member-name normalization changes logical meaning relative to `raw_name` | Warning on `archivey.normalization` |

### Requirement: Warning logs are ordered projections of diagnostics

Every library advisory logged at WARNING SHALL originate as a `Diagnostic`; logging
MUST NOT be a second source of truth. For `COLLECT` and `RAISE`, the WARNING record
SHALL emit after exact counts/retention update and before diagnostic callback or
escalation. For `IGNORE`, no WARNING record SHALL emit.

The record SHALL use the existing named `archivey.*` logger for the event and SHALL
expose `diagnostic_code` as the code string and `diagnostic_occurrence_id` as the
opaque id in `LogRecord.extra`. Human message text is not a byte-for-byte
compatibility contract.

Logging handlers are application code. The system SHALL hold no diagnostic
collector, reader, stream, backend, or registry lock while invoking handlers. If a
handler raises, normal Python logging semantics apply: the exception propagates and
later callback/escalation steps for that occurrence do not run.

#### Scenario: diagnostic-log matrix

| Case | Expected |
| --- | --- |
| Warning-severity diagnostic resolves to default `COLLECT` | Counts/retention update, then one WARNING with `diagnostic_code` and `diagnostic_occurrence_id` |
| Warning-severity diagnostic resolves to `IGNORE` | Exact count increments; no log record |
| Application logging handler runs | No Archivey collector/reader/stream/backend/registry lock is held |
| Logging handler raises | Handler exception propagates; callback/escalation for the occurrence do not run |
