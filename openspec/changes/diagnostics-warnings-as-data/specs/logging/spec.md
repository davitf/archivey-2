# logging — diagnostics relationship delta

## ADDED Requirements

### Requirement: Logged advisories are the projection of diagnostics

Every advisory the library logs at WARNING (name normalization, detection conflict, rewind
cost, skipped member, invalid timestamp, unverifiable digest, and the like) SHALL correspond
to a `Diagnostic` (see the `diagnostics` capability): logging is the human-facing projection
of the diagnostic, not a separate source of truth. The logger hierarchy and the "the library
installs no handlers, levels, or formatters" rule are unchanged, and an application that
configures only logging SHALL observe the same log output as before this capability existed.

#### Scenario: logging-only application sees unchanged output

- **WHEN** an application configures the `archivey` logger but uses none of the diagnostics data/callback/escalation channels
- **THEN** it observes the same WARNING records as before, each corresponding to an emitted `Diagnostic`
