# Logging

## Purpose

Logging gives callers visibility into internal library events — format detection, path normalization, extraction decisions, and backend-level debugging — through the standard Python `logging` module under a named logger hierarchy. The library is a well-behaved logging citizen: it emits records but never configures handlers, levels, or formatters itself, leaving all output policy to the application.

## Requirements

### Requirement: Logging Under the archivey Logger Hierarchy

The system SHALL emit all log messages via `logging.getLogger("archivey")` and its named children. The library SHALL NOT configure any handlers, levels, or formatters — that is left entirely to the application.

The named child loggers are:

| Logger | Events |
|---|---|
| `archivey.detection` | Format detection events |
| `archivey.normalization` | Path normalization changes (warnings when `name` differs from `raw_name`) |
| `archivey.extraction` | Extraction events and filter decisions |
| `archivey.backends.*` | Backend-specific debug messages |

#### Scenario: library emits no output by default

- **WHEN** the application has not configured any handlers on the `archivey` logger or its ancestors
- **THEN** no output is produced, in accordance with Python's default "no handler" behaviour; the library never installs a handler itself

#### Scenario: format detection conflict logged as WARNING

- **WHEN** format detection finds a magic-byte match that conflicts with the file extension
- **THEN** a `logging.WARNING` is emitted on `archivey.detection`

#### Scenario: path normalization change logged

- **WHEN** normalizing a member's `name` changes its logical meaning compared to `raw_name`
- **THEN** a warning is emitted via `archivey.normalization`

### Requirement: Warning logs are ordered projections of diagnostics

Every library advisory logged at WARNING SHALL originate as a `Diagnostic`; logging SHALL
not be a second source of truth. For `COLLECT` and `RAISE`, the WARNING record SHALL be
emitted after exact counts/retention are updated and before the diagnostic callback or
escalation. For `IGNORE`, no WARNING record SHALL be emitted.

The record SHALL use the existing named `archivey.*` logger appropriate to the event and
SHALL expose `diagnostic_code` as the code's string value and
`diagnostic_occurrence_id` as the opaque id in `LogRecord.extra`. Human message text is
not a byte-for-byte compatibility contract. The existing rule remains: the library
installs no handlers, levels, filters, or formatters.

Logging handlers are application code. The system SHALL hold no collector, reader,
stream, backend, or registry lock while invoking them. If a configured logging handler
raises, normal Python logging semantics apply: that exception propagates and the later
callback/escalation steps for the occurrence do not run.

#### Scenario: default collected diagnostic logs

- **WHEN** a warning-severity diagnostic resolves to the default `COLLECT` disposition
- **THEN** its exact count/retention state is updated and one WARNING record carrying `diagnostic_code` and `diagnostic_occurrence_id` is emitted on the appropriate existing `archivey.*` logger

#### Scenario: ignored diagnostic is not logged

- **WHEN** a warning-severity diagnostic resolves to `IGNORE`
- **THEN** its exact count increments but no log record is emitted

#### Scenario: logging runs without internal locks

- **WHEN** an application logging handler runs for a diagnostic
- **THEN** no diagnostic collector, reader, stream, backend, or registry lock is held by Archivey
