# diagnostics — lifecycle-aware warnings as data

## ADDED Requirements

### Requirement: Immutable diagnostic values with stable codes and safe typed context

The system SHALL represent every advisory event as an immutable `Diagnostic` carrying an
opaque process-local `occurrence_id`, a stable string-enum `DiagnosticCode`, a
`DiagnosticSeverity`, a human `message`, and a code-specific frozen `DiagnosticContext`.
Codes are the machine contract; messages are not stable.

Contexts SHALL form a closed typed union and contain only JSON-safe immutable scalar/tuple
values. Raw bytes, when necessary, SHALL use an explicitly named base64 string field.
`Diagnostic.to_dict()` and every context's `to_dict()` SHALL return values accepted by
`json.dumps()` without a custom encoder.

Every context SHALL have a literal string `kind` discriminator, and every initial code
SHALL map to exactly this variant and field set:

| Code | Variant and required fields |
|---|---|
| `MEMBER_NAME_NORMALIZED` | `NameNormalizationContext`: `kind="name_normalization"`, `archive_name: str \| None`, `member_name: str`, `member_id: int \| None`, `raw_name_base64: str \| None`, `presented_name: str`, `normalized_name: str` |
| `FORMAT_EXTENSION_CONFLICT` | `FormatConflictContext`: `kind="format_conflict"`, `source_name: str \| None`, `extension: str \| None`, `extension_format: str`, `detected_format: str` |
| `SCAN_DIRECTORY_VANISHED` | `ScanRaceContext`: `kind="scan_race"`, `archive_name: str \| None`, `relative_path: str`, `entry_kind="directory"` |
| `SCAN_ENTRY_VANISHED` | `ScanRaceContext`: `kind="scan_race"`, `archive_name: str \| None`, `relative_path: str`, `entry_kind="entry"` |
| `ARCHIVE_EOF_MARKER_MISSING` | `ArchiveEofContext`: `kind="archive_eof"`, `archive_name: str \| None`, `format: str`, `expected_marker: str`, `expected_bytes: int`, `observed_bytes: int`, `observed_kind: str` |
| `MEMBER_TIMESTAMP_INVALID` | `MemberTimestampContext`: `kind="member_timestamp"`, `archive_name: str \| None`, `member_name: str`, `member_id: int \| None`, `field: str`, `source: str`, `value_repr: str` |
| `SYMLINK_TARGET_UNAVAILABLE` | `SymlinkTargetContext`: `kind="symlink_target"`, `archive_name: str \| None`, `member_name: str`, `member_id: int \| None`, `reason: str` |
| `DIGEST_UNVERIFIABLE` | `DigestContext`: `kind="digest"`, `archive_name: str \| None`, `member_name: str`, `member_id: int \| None`, `algorithm: str`, `reason: str` |
| `SEEK_INDEX_DEGRADED` | `SeekIndexContext`: `kind="seek_index"`, `archive_name: str \| None`, `member_name: str \| None`, `member_id: int \| None`, `codec: str`, `scan: str`, `error_type: str` |
| `STREAM_REWIND_REDECOMPRESSES` | `StreamRewindContext`: `kind="stream_rewind"`, `archive_name: str \| None`, `member_name: str \| None`, `member_id: int \| None`, `codec: str`, `from_offset: int`, `to_offset: int`, `accelerator: str \| None` |
| `EXTRACTION_MEMBER_REJECTED` | `ExtractionOutcomeContext`: `kind="extraction_outcome"`, `archive_name: str \| None`, `member_name: str`, `member_id: int \| None`, `status="rejected"`, `error_type: str`, `failure_group_id: str \| None`, `failure_group_size: int \| None` |
| `EXTRACTION_MEMBER_FAILED` | `ExtractionOutcomeContext`: `kind="extraction_outcome"`, `archive_name: str \| None`, `member_name: str`, `member_id: int \| None`, `status="failed"`, `error_type: str`, `failure_group_id: str \| None`, `failure_group_size: int \| None` |

`DiagnosticContext` SHALL be exactly the union of the ten named variants in this table;
backend-defined mappings or unregistered context variants SHALL be rejected.
`observed_kind` SHALL initially be one of `"absent"`, `"short"`, or `"nonzero"`;
`expected_marker` SHALL be a symbolic value such as `"two_zero_blocks"`, not raw bytes.
`member_id` MAY be `None` only if emission precedes registration.
`failure_group_id`/`failure_group_size` SHALL both be present only when multiple hardlink
results share one failed source and SHALL otherwise both be `None`.

No diagnostic message, context, log projection, callback argument, or escalation error
SHALL contain a password, password candidate, password-provider return value, encryption
key, key-derivation material, or decrypted secret bytes.

Copies of one occurrence MAY be retained on multiple surfaces by value. They SHALL carry
the same `occurrence_id`; the system SHALL NOT guarantee Python object identity, and ids
need not be stable across runs.

#### Scenario: normalization produces a safe typed value

- **WHEN** a member name is normalized
- **THEN** the diagnostic uses `MEMBER_NAME_NORMALIZED`, carries a typed member/name context that serializes directly to JSON, and contains no backend object or mutable mapping

#### Scenario: occurrence correlation does not depend on object identity

- **WHEN** one occurrence is retained in an aggregate and on its member
- **THEN** both values carry the same opaque `occurrence_id` and compare by value, but callers are not promised that they are the same Python object

#### Scenario: password material is prohibited

- **WHEN** a diagnostic describes an unavailable encrypted symlink target
- **THEN** it may carry reason `"password_required"` and the member name, but no password, candidate, provider return value, key, or decrypted target bytes

### Requirement: Exact bounded diagnostic summaries

The system SHALL expose immutable `DiagnosticSummary` snapshots containing
`total_count`, exact immutable per-code `counts`, retained occurrences in emission order,
and `dropped_count`. Counts SHALL include every emitted event regardless of disposition or
retention. `dropped_count` SHALL equal aggregate occurrences not retained.

Each standalone detection, reader lifetime, top-level extraction operation, or standalone
stream SHALL enforce one `ArchiveyConfig.max_retained_diagnostic_references` budget
(default 256) across every diagnostic reference the library retains for that collector.
An aggregate retained occurrence consumes one slot; one eligible object attachment
consumes another. Allocation
order SHALL be aggregate first, then the one most-specific attachment. No attachment is
created unless its aggregate occurrence was retained. Exact counters do not consume
retention slots.

Snapshots SHALL be freshly created, bounded, and never mutated. Caller-retained snapshots
and caller-created member copies are caller-owned and outside the library-retention bound.
An internal operation watermark consumes no slot and copies no occurrence; a ranged
summary computes exact counter deltas and selects from already-retained aggregate entries.

#### Scenario: member occurrences consume aggregate and attachment slots

- **WHEN** a member-specific diagnostic is emitted with at least two budget slots remaining
- **THEN** one slot retains it in the aggregate and one attaches it to the member; with only one slot remaining only the aggregate retains it

#### Scenario: exhausted retention keeps exact counts

- **WHEN** diagnostics continue after every retention slot is consumed
- **THEN** no further detail or attachment is retained, while `total_count` and every per-code count remain exact

#### Scenario: snapshots are immutable points in time

- **WHEN** a caller stores `before = reader.diagnostics`, more events occur, and it reads `after = reader.diagnostics`
- **THEN** `before` is unchanged and `after` includes the later exact counts in emission order

### Requirement: Lifecycle-aware aggregation and attachment

A standalone detection call SHALL create one collector and return its final summary on
`FormatInfo`. `open_archive()` SHALL instead create one prospective-reader collector
before automatic detection, pass that same collector into detection, and transfer
ownership of it to the successfully opened reader. It SHALL NOT seed, merge, replay, or
copy events into another collector. The detection watermark and reader views use the same
exact counters, retained tuple, occurrence ids, order, and one-time budget charges.
`ArchiveReader.diagnostics` SHALL snapshot every event owned by that reader over its
lifetime. A reader-owned `ArchiveStream.diagnostics` SHALL return an operation-filtered
view over the same collector without separately retaining the same aggregate occurrence;
a standalone archive stream SHALL own a stream-lifetime collector.

Only natural member-metadata diagnostics SHALL be eligible for
`ArchiveMember.diagnostics`, under the shared budget. `ExtractionResult` SHALL have no
diagnostics field: its status/error are authoritative and extraction diagnostics remain in
the report/reader aggregate. Detection conflict SHALL attach to `FormatInfo`. Runtime
rewind, seek-index degradation, directory scan race, and archive EOF diagnostics SHALL
remain aggregate-only and SHALL NOT be attached to frozen `CostReceipt` or `ArchiveInfo`.

#### Scenario: automatic detection transfers one collector

- **WHEN** `open_archive()` detects a magic/extension conflict and successfully creates a reader
- **THEN** detection and the reader use one collector and budget, so the conflict consumes one aggregate slot and appears in the reader's cumulative summary without seeding or copying retained references

#### Scenario: one-shot extraction shares one operation collector

- **WHEN** top-level `extract()` detects, opens, reads, and extracts an archive
- **THEN** those phases use one collector created before detection and the returned report summarizes the original operation watermark without merging phase-local collectors

#### Scenario: runtime rewind is visible at the right lifetimes

- **WHEN** a reader-owned stream rewinds through an index-less codec
- **THEN** the occurrence appears in the stream operation snapshot and cumulative reader snapshot, and no frozen `CostReceipt` or `ArchiveInfo` is mutated

### Requirement: Complete per-code policy and delivery contract

The system SHALL provide a frozen `DiagnosticPolicy` with a default disposition and
immutable per-code overrides. The only dispositions are `IGNORE`, `COLLECT`, and `RAISE`;
severity- and logger-based matching are not part of this capability.

Disposition behavior SHALL be:

| Disposition | Exact counts | Retain/attach | WARNING log | Callback | Raise |
|---|---|---|---|---|---|
| `IGNORE` | yes | no | no | no | no |
| `COLLECT` | yes | budget permitting | yes | if configured | no |
| `RAISE` | yes | budget permitting | yes | if configured | `DiagnosticRaisedError` |

For each event the system SHALL validate the typed payload, resolve policy, then under its
collector lock allocate the occurrence id, construct the immutable value, and update
counts/retention. It SHALL release all internal locks, log, invoke the synchronous callback
on the calling thread, then escalate. Logs and callbacks SHALL therefore observe
already-updated snapshot state and occur in emission order.

A logging-handler exception SHALL propagate and prevent later callback/escalation steps.
A callback exception SHALL propagate unchanged, SHALL NOT be governed by
`OnError.CONTINUE`, and SHALL prevent the later `DiagnosticRaisedError`; the operation
nevertheless halts. The system SHALL hold no collector, reader, stream, backend, or
registry lock while calling logging handlers or the callback.

Callbacks MAY inspect immutable arguments and read diagnostics snapshots. Operational
reentry on the same emitting reader/stream SHALL fail with `UnsupportedOperationError`;
snapshot reads are allowed, and operations on another reader are allowed.

#### Scenario: ignored event is still counted

- **WHEN** a code resolves to `IGNORE`
- **THEN** its exact count increments, but it is not retained, attached, logged, delivered to the callback, or raised

#### Scenario: callback can observe current state without a lock

- **WHEN** a callback reads `reader.diagnostics`
- **THEN** it sees the current occurrence already counted/retained and no internal lock is held during the callback

#### Scenario: callback failure halts unchanged

- **WHEN** a callback raises `CallbackError` while processing a `RAISE` diagnostic
- **THEN** `CallbackError` propagates unchanged, the occurrence remains counted/delivered through earlier steps, no `DiagnosticRaisedError` replaces it, and extraction cannot continue under `OnError.CONTINUE`

#### Scenario: same-reader operational reentrancy fails loudly

- **WHEN** the callback attempts to start another read/extraction operation on the same currently emitting reader
- **THEN** `UnsupportedOperationError` is raised rather than deadlocking or recursively changing occurrence order

### Requirement: Complete initial warning taxonomy

The initial `DiagnosticCode` set SHALL cover all 17 current warning calls:

`MEMBER_NAME_NORMALIZED`, `FORMAT_EXTENSION_CONFLICT`,
`SCAN_DIRECTORY_VANISHED`, `SCAN_ENTRY_VANISHED`,
`ARCHIVE_EOF_MARKER_MISSING`, `MEMBER_TIMESTAMP_INVALID`,
`SYMLINK_TARGET_UNAVAILABLE`, `DIGEST_UNVERIFIABLE`,
`SEEK_INDEX_DEGRADED`, `STREAM_REWIND_REDECOMPRESSES`,
`EXTRACTION_MEMBER_REJECTED`, and `EXTRACTION_MEMBER_FAILED`.

The 17-site mapping and required context/attachment are authoritative in
the closed table above and the warning-site audit in `design.md` section 8. Multiple call
sites SHALL share a code only when they have the same machine meaning; typed context
distinguishes source variants. The extraction-code count unit SHALL be one continued
result with the matching status. Under `IGNORE` or `COLLECT`, one failed hardlink source
that causes `N` failed link results SHALL emit `N` `EXTRACTION_MEMBER_FAILED`
occurrences, correlated by shared failure-group fields. Under `RAISE`, the first ordered
occurrence escalates and no completed-result count guarantee applies. No unused future
code is reserved.

#### Scenario: every warning-only source is migrated

- **WHEN** implementation of this change is complete
- **THEN** each of the 17 warning calls emits one of the initial codes through the central path, and no current advisory remains logging-only
