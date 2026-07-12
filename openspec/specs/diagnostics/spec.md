# Diagnostics

## Purpose

Advisory library events as immutable, policy-controlled data: stable codes, typed
JSON-safe context, bounded retention, and lifecycle-aware aggregation across
detection, readers, streams, and extraction.

## Related specs

| Spec | Relationship |
| --- | --- |
| `archive-reading` | `reader.diagnostics` / stream-filtered views (observables) |
| `format-detection` | Standalone `FormatInfo.diagnostics`; open handoff of one collector |
| `safe-extraction` | Extraction report ranges over the shared collector |
| `access-mode-and-cost` | Runtime events must not mutate `CostReceipt` |
| `logging` | WARNING projection; handlers run unlocked |
| `reader-concurrency` | Callbacks / providers hold no Archivey locks |

## Requirements

### Requirement: Immutable diagnostic values with stable codes and safe typed context

Every advisory event SHALL be an immutable `Diagnostic`: opaque process-local
`occurrence_id`, stable `DiagnosticCode`, `DiagnosticSeverity`, human `message`,
and code-specific frozen `DiagnosticContext`. Codes are the machine contract;
messages are not stable.

Contexts are a closed typed union of JSON-safe immutable scalar/tuple values.
Raw bytes use an explicitly named base64 field. `to_dict()` on diagnostic and
context SHALL be `json.dumps`-safe without a custom encoder.

| Code | Variant and required fields |
| --- | --- |
| `MEMBER_NAME_NORMALIZED` | `NameNormalizationContext`: `kind="name_normalization"`, `archive_name`, `member_name`, `member_id`, `raw_name_base64`, `presented_name`, `normalized_name` |
| `FORMAT_EXTENSION_CONFLICT` | `FormatConflictContext`: `kind="format_conflict"`, `source_name`, `extension`, `extension_format`, `detected_format` |
| `SCAN_DIRECTORY_VANISHED` | `ScanRaceContext`: `kind="scan_race"`, `archive_name`, `relative_path`, `entry_kind="directory"` |
| `SCAN_ENTRY_VANISHED` | `ScanRaceContext`: `kind="scan_race"`, `archive_name`, `relative_path`, `entry_kind="entry"` |
| `ARCHIVE_EOF_MARKER_MISSING` | `ArchiveEofContext`: `kind="archive_eof"`, `archive_name`, `format`, `expected_marker`, `expected_bytes`, `observed_bytes`, `observed_kind` |
| `MEMBER_TIMESTAMP_INVALID` | `MemberTimestampContext`: `kind="member_timestamp"`, `archive_name`, `member_name`, `member_id`, `field`, `source`, `value_repr` |
| `SYMLINK_TARGET_UNAVAILABLE` | `SymlinkTargetContext`: `kind="symlink_target"`, `archive_name`, `member_name`, `member_id`, `reason` |
| `DIGEST_UNVERIFIABLE` | `DigestContext`: `kind="digest"`, `archive_name`, `member_name`, `member_id`, `algorithm`, `reason` |
| `SEEK_INDEX_DEGRADED` | `SeekIndexContext`: `kind="seek_index"`, `archive_name`, `member_name`, `member_id`, `codec`, `scan`, `error_type` |
| `STREAM_REWIND_REDECOMPRESSES` | `StreamRewindContext`: `kind="stream_rewind"`, `archive_name`, `member_name`, `member_id`, `codec`, `from_offset`, `to_offset`, `accelerator` |
| `EXTRACTION_MEMBER_REJECTED` | `ExtractionOutcomeContext`: `kind="extraction_outcome"`, `…`, `status="rejected"`, `error_type`, `failure_group_id`, `failure_group_size` |
| `EXTRACTION_MEMBER_FAILED` | `ExtractionOutcomeContext`: `kind="extraction_outcome"`, `…`, `status="failed"`, `error_type`, `failure_group_id`, `failure_group_size` |

(`str | None` / `int | None` as in the typed variants.) `DiagnosticContext` is
exactly this union — no backend-defined variants. `observed_kind` ∈
`{"absent","short","nonzero"}`. `expected_marker` is symbolic (e.g.
`"two_zero_blocks"`). `member_id` MAY be `None` only before registration.
`failure_group_id`/`failure_group_size` both set only when multiple hardlink
results share one failed source; else both `None`.

No diagnostic surface SHALL contain passwords, candidates, provider returns, keys,
KDF material, or decrypted secrets.

Copies on multiple surfaces MAY share `occurrence_id` by value; object identity
and cross-run id stability are not promised.

#### Scenario: value-model matrix

| Case | Expected |
| --- | --- |
| Name normalization | `MEMBER_NAME_NORMALIZED` + typed JSON-safe context; no backend/mutable mapping |
| Same occurrence on aggregate + member | Same `occurrence_id`; value equality; no object-identity promise |
| Encrypted symlink unavailable | May use reason `"password_required"` + member name; no secret material |

### Requirement: Exact bounded diagnostic summaries

`DiagnosticSummary` snapshots SHALL expose `total_count`, exact per-code `counts`,
retained occurrences in emission order, and `dropped_count`. Counts include every
emitted event regardless of disposition/retention. `dropped_count` = aggregate
occurrences not retained.

Each standalone detection, reader lifetime, top-level extraction, or standalone
stream SHALL enforce one `ArchiveyConfig.max_retained_diagnostic_references`
budget (default 256) across every library-retained reference for that collector.
Aggregate retention = one slot; one eligible object attachment = another.
Order: aggregate first, then most-specific attachment. No attachment without a
retained aggregate. Exact counters do not consume slots.

Snapshots are freshly created, bounded, never mutated. Caller-retained snapshots
and caller-created member copies are outside the library budget.

**Watermark (implementer):** an internal operation watermark consumes no slot and
copies no occurrence; a ranged summary computes counter deltas and selects from
already-retained aggregate entries.

#### Scenario: retention matrix

| Case | Expected |
| --- | --- |
| Member diagnostic, ≥2 slots left | Aggregate + member attachment; 1 slot → aggregate only |
| Budget exhausted, more events | No further detail/attachment; counts stay exact |
| `before = reader.diagnostics`, more events, `after = …` | `before` unchanged; `after` includes later counts in order |

### Requirement: Lifecycle-aware aggregation and attachment

The system SHALL own diagnostic aggregation per lifetime as follows:

| Lifetime | Collector ownership |
| --- | --- |
| Standalone `detect_format` | One collector; final summary on `FormatInfo` |
| `open_archive` + auto-detect | Prospective-reader collector created before detection, passed in, owned by successful reader — no seed/merge/replay/copy. Same counters, retained tuple, ids, order, one-time budget charges |
| Reader-owned stream | Operation-filtered view over the reader collector (no second aggregate retain) |
| Standalone stream | Own stream-lifetime collector |
| Top-level `extract()` | One collector for the whole call (see `safe-extraction`) |

Attachment rules:

- Natural member-metadata diagnostics MAY attach to `ArchiveMember.diagnostics`
  under the shared budget.
- `ExtractionResult` has **no** diagnostics field (status/error authoritative;
  extraction events stay in report/reader aggregate).
- Detection conflict attaches to `FormatInfo`.
- Runtime rewind, seek-index degradation, scan race, archive EOF: aggregate-only —
  never attached to frozen `CostReceipt` or `ArchiveInfo`.

#### Scenario: lifetime matrix

| Case | Expected |
| --- | --- |
| `open_archive` detects conflict, opens reader | One collector/budget; conflict one aggregate slot; visible on reader summary; no copy |
| Top-level `extract()` detect→open→extract | One collector from before detection; report is watermark range; no phase-local merge |
| Reader-owned stream rewinds | On stream op snapshot + cumulative reader; `CostReceipt`/`ArchiveInfo` unchanged |

### Requirement: Complete per-code policy and delivery contract

The system SHALL provide a frozen `DiagnosticPolicy` with a default disposition
and immutable per-code overrides. The only dispositions SHALL be `IGNORE`,
`COLLECT`, and `RAISE` (no severity/logger matching).

| Disposition | Counts | Retain/attach | WARNING log | Callback | Raise |
| --- | --- | --- | --- | --- | --- |
| `IGNORE` | yes | no | no | no | no |
| `COLLECT` | yes | budget permitting | yes | if configured | no |
| `RAISE` | yes | budget permitting | yes | if configured | `DiagnosticRaisedError` |

Per event: validate typed payload → resolve policy → under collector lock allocate
id, build immutable value, update counts/retention → **release all locks** → log →
synchronous callback on calling thread → escalate. Logs/callbacks see
already-updated state, in emission order.

| Failure | Behavior |
| --- | --- |
| Logging-handler exception | Propagates; blocks later callback/escalation |
| Callback exception | Propagates unchanged; not under `OnError.CONTINUE`; blocks later `DiagnosticRaisedError`; operation still halted |

No collector/reader/stream/backend/registry lock while calling handlers/callbacks.
Callbacks MAY read snapshots; same-emitting-reader/stream operational reentry →
`UnsupportedOperationError`; other readers OK.

#### Scenario: policy / delivery matrix

| Case | Expected |
| --- | --- |
| Code → `IGNORE` | Count++; no retain/attach/log/callback/raise |
| Callback reads `reader.diagnostics` | Sees current event counted/retained; no lock held |
| Callback raises during `RAISE` | Callback error propagates; no replacement `DiagnosticRaisedError`; no `OnError.CONTINUE` |
| Callback starts op on same emitting reader | `UnsupportedOperationError` |

### Requirement: Complete initial warning taxonomy

The initial `DiagnosticCode` set SHALL cover the library's advisory emissions via
the codes in the closed table above. Multiple call sites share a code only with the
same machine meaning; typed context distinguishes variants.

Extraction count unit = one continued result with matching status. Under
`IGNORE`/`COLLECT`, one failed hardlink source causing `N` failed link results →
`N` `EXTRACTION_MEMBER_FAILED` occurrences with shared failure-group fields. Under
`RAISE`, the first ordered occurrence escalates (no completed-result count
guarantee). No unused future codes reserved.

#### Scenario: taxonomy coverage

| Case | Expected |
| --- | --- |
| Advisory path that formerly logged only | Emits one of the initial codes through the central path |
