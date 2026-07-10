# diagnostics — warnings as queryable data

## ADDED Requirements

### Requirement: Diagnostic record with stable codes

The system SHALL represent every non-fatal advisory it produces (a normalized name, a
detection conflict, a rewind cost, a skipped member, a guessed password, an invalid
timestamp, an unverifiable digest, a vanished scan entry, trailing data, and the like) as
a **`Diagnostic`** value carrying at least: a machine-stable `code` from an enumerated set,
a `severity`, a human-readable `message`, and typed context identifying what it concerns
(the member name and/or byte offset, and the relevant before/after values such as the raw
and normalized name). The `code` values SHALL be stable across releases so callers can
branch on them programmatically; `message` is for human display only and is not part of the
stable contract.

#### Scenario: a normalized name is a diagnostic with a stable code and context

- **WHEN** normalizing a member's `name` changes its meaning relative to `raw_name`
- **THEN** a `Diagnostic` with the stable normalization `code` is produced, carrying the member's `raw_name` and normalized `name`

### Requirement: Diagnostics attached to their natural surface

A diagnostic that concerns a specific result object the caller already holds SHALL be
reachable from that object: a per-member diagnostic (normalization, timestamp, digest) from
its `ArchiveMember`; a detection diagnostic from the archive's format info; a rewind-cost
diagnostic from the relevant `CostReceipt`; an extraction diagnostic (skipped member,
guessed password) from that member's `ExtractionResult`. The object-attached diagnostic and
the aggregated collection (below) SHALL reference the **same** `Diagnostic` value, not
duplicated data.

#### Scenario: a skipped member carries its diagnostic on the extraction result

- **WHEN** a member is skipped during extraction under `OnError.CONTINUE`
- **THEN** that member's `ExtractionResult` exposes the skip as a `Diagnostic`, and the same value appears in the operation's aggregated diagnostics

### Requirement: Per-operation aggregation

The reader SHALL expose the diagnostics emitted during an open/read operation as a
queryable collection, and `extract()` SHALL return the diagnostics emitted during
extraction alongside the per-member results. The collection SHALL include diagnostics that
have no per-object surface (for example a directory that vanished during a scan, or trailing
bytes after the archive end). The retained collection SHALL be bounded and SHALL expose
exact per-`code` counts, so an archive that emits a very large number of diagnostics cannot
turn the collection into an unbounded memory cost.

#### Scenario: a diagnostic with no natural object is still reachable

- **WHEN** a directory vanishes during a directory scan
- **THEN** the corresponding `Diagnostic` appears in the operation's aggregated diagnostics even though no member object represents it

#### Scenario: mass diagnostics are counted, not unboundedly retained

- **WHEN** an archive causes a very large number of same-`code` diagnostics
- **THEN** the aggregated collection retains a bounded number of them and still reports an exact count for that `code`

### Requirement: Eager delivery via callback

The library configuration SHALL accept an optional diagnostic callback that is invoked with
each `Diagnostic` at the point it is emitted, so a caller performing a long streaming or
extraction operation can react to diagnostics as they occur rather than only after the
operation completes. When no callback is configured, diagnostics are still aggregated and
logged.

#### Scenario: callback receives diagnostics during a streaming pass

- **WHEN** a diagnostic callback is configured and diagnostics arise during a streaming read
- **THEN** the callback is invoked for each diagnostic as it occurs, before the operation completes

### Requirement: Configurable escalation, non-fatal by default

Diagnostics SHALL be non-fatal by default: producing a diagnostic SHALL NOT by itself raise,
and with default configuration the observable behaviour is unchanged except that diagnostics
are also queryable as data. The configuration SHALL allow a caller to select a disposition
per `code` (or severity) of ignore, collect, or **raise**; a `raise` disposition SHALL cause
the diagnostic to surface as a typed exception at the point it is emitted, so a strict caller
can make a chosen advisory (for example a normalized name, a detection conflict, or a guessed
password) a hard error. Genuine failures remain exceptions regardless of this policy.

#### Scenario: default configuration does not change control flow

- **WHEN** the library runs with default configuration on input that produces diagnostics
- **THEN** no diagnostic causes an exception; the operation proceeds exactly as before, with the diagnostics additionally available as data and via logging

#### Scenario: a caller escalates a chosen code to an error

- **WHEN** a caller sets the disposition for the normalization `code` to raise, and a member name is normalized
- **THEN** a typed exception carrying that `Diagnostic` is raised at the point of normalization
