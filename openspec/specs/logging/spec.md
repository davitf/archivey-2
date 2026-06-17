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
