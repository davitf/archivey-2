# logging — diagnostic projection

## ADDED Requirements

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
