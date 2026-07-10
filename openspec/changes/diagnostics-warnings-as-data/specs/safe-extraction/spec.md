# safe-extraction — first-class report and diagnostic semantics

## MODIFIED Requirements

### Requirement: One-Shot Extraction API

The top-level API SHALL return a first-class report rather than a bare result list:

```python
archivey.extract(
    source: str | Path | BinaryIO | Sequence[str | Path | BinaryIO],
    dest: str | Path,
    *,
    policy: ExtractionPolicy = ExtractionPolicy.STRICT,
    overwrite: OverwritePolicy = OverwritePolicy.ERROR,
    on_error: OnError = OnError.STOP,
    format: ArchiveFormat | None = None,
    password: str | bytes | Sequence[str | bytes] | PasswordProvider | None = None,
    encoding: str | None = None,
    on_progress: Callable[[ExtractionProgress], None] | None = None,
    config: ArchiveyConfig | None = None,
    limits: ExtractionLimits | None = None,
) -> ExtractionReport
```

The one-shot call SHALL create one collector, one retention budget, and its report
watermark before format detection. It SHALL pass that collector through internal
detection, backend open, read, and extraction; the temporary reader owns it during the
call. No phase creates a second collector, seeds/copies retained occurrences, or resets
the budget. The final report summary SHALL span the original watermark and therefore
include all events caused by the call exactly once.

`results` SHALL be an immutable tuple. If an always-stop condition or `OnError.STOP`
raises, no report is returned.
The function still extracts all members and deliberately has no `members=` selector;
subset extraction uses `ArchiveReader.extract_all()` on an already-open reader.
`source`, `password`, `encoding`, config/limits precedence, default STRICT policy,
default ERROR overwrite behavior, and automatic streaming mode for a non-seekable source
retain their existing contracts.

#### Scenario: successful one-shot extraction returns one report

- **WHEN** `archivey.extract(source, dest)` completes
- **THEN** it returns `ExtractionReport(results=(...), diagnostics=...)`, including any detection and extraction diagnostics caused by the call

#### Scenario: one-shot phases share one collector

- **WHEN** detection emits one retained conflict and extraction later emits one retained failure
- **THEN** the report uses one occurrence order and budget from before detection, with neither event copied or re-retained at a phase handoff

#### Scenario: one-shot extraction from a non-seekable pipe

- **WHEN** `archivey.extract(pipe, dest)` receives a non-seekable supported source
- **THEN** it opens in streaming mode automatically and extracts in one forward pass

#### Scenario: subset extraction goes through an open reader

- **WHEN** a caller wants only some members
- **THEN** they open the archive and call `reader.extract_all(dest, members=...)`; the top-level function has no selection parameter

### Requirement: Per-Reader Extract-All Helper

`ArchiveReader.extract_all()` SHALL return:

```python
def extract_all(
    dest: str | Path,
    *,
    members: MemberSelector | None = None,
    filter: MemberFilter | None = None,
    policy: ExtractionPolicy = ExtractionPolicy.STRICT,
    overwrite: OverwritePolicy = OverwritePolicy.ERROR,
    on_error: OnError = OnError.STOP,
    on_progress: Callable[[ExtractionProgress], None] | None = None,
    config: ArchiveyConfig | None = None,
    limits: ExtractionLimits | None = None,
) -> ExtractionReport: ...
```

The reader records a collector watermark when the call begins. The returned report's
diagnostic summary SHALL contain exact count/retained deltas for this extraction call only,
while `reader.diagnostics` remains cumulative and includes earlier and later events.
An extraction config override changes new-event policy/callback behavior but uses the
reader's existing collector and retention maximum.
Selection, filter ordering, one-pass selected extraction, reader-config inheritance, and
per-call limits precedence retain their existing contracts. There is still no
single-member `reader.extract()` method.

#### Scenario: extraction report excludes prior reader events

- **WHEN** a reader emits a diagnostic before `extract_all()` and another during extraction
- **THEN** the report summary includes only the extraction occurrence, while `reader.diagnostics` includes both

#### Scenario: selected solid members extract in one pass

- **WHEN** `reader.extract_all(dest, members=["a", "b"])` runs on a solid archive
- **THEN** only those members are selected and their data is extracted in one decompression pass

### Requirement: Extraction reads limits and strictness from the configuration object

`archivey.extract()` and `ArchiveReader.extract_all()` SHALL accept
`config: ArchiveyConfig | None` and `limits: ExtractionLimits | None`. Per-call `limits`
takes precedence over `config.extraction_limits`, then the reader/library default.
`ExtractionLimits.UNLIMITED` disables all four guards. Extraction policy/overwrite/error/
progress/member-selection arguments remain keyword operational arguments outside config.

Top-level `extract()` uses the supplied config for its one collector. `extract_all()` uses
the reader config by default; an explicit config may change new-event diagnostic policy
and callback but SHALL use the existing collector and its original retention maximum.

Both APIs return `ExtractionReport`, whose result tuple is always accumulated. There is no
no-tracking mode in this capability. The report diagnostic summary is bounded by the
collector's shared budget.

#### Scenario: limits override does not split diagnostics

- **WHEN** `extract_all(limits=...)` overrides bomb limits on an existing reader
- **THEN** the limits apply to that extraction while its report remains a watermark range over the reader's existing diagnostic collector

### Requirement: Per-ArchiveMember ExtractionResult with Status

`ExtractionReport.results` SHALL contain one `ExtractionResult` for every selected member
the extraction coordinator processes when the operation completes. This includes a member
rejected by universal/policy safety checks before the user filter runs. Selector-excluded
members are outside the operation and SHALL have no result.

```python
@dataclass(frozen=True)
class ExtractionResult:
    member: ArchiveMember
    path: Path | None
    status: ExtractionStatus
    error: ArchiveyError | OSError | None = None

class ExtractionStatus(str, Enum):
    EXTRACTED = "extracted"
    SKIPPED = "skipped"
    REJECTED = "rejected"
    FAILED = "failed"
```

Statuses SHALL mean:

- `EXTRACTED`: an entry was successfully created; `path` is that path and `error=None`.
- `SKIPPED`: writing was intentionally bypassed because the user filter returned `None`
  or `OverwritePolicy.SKIP` found an existing destination; `path=None`, `error=None`.
- `REJECTED`: a `FilterRejectionError` blocked the member under
  `OnError.CONTINUE`; `path=None`, `error` is that rejection.
- `FAILED`: another per-member `ArchiveyError` or permitted filesystem `OSError` failed
  under `OnError.CONTINUE`; `path=None`, `error` is that failure.

`SKIPPED` is not a failure and SHALL NOT itself emit a diagnostic. A continued rejection
or failure SHALL emit `EXTRACTION_MEMBER_REJECTED` or
`EXTRACTION_MEMBER_FAILED`. Those occurrences live only in the extraction/reader
aggregate. `ExtractionResult` deliberately has no diagnostics field: `status` and `error`
are the complete per-result outcome, independent of detail-retention budget.

Each continued `REJECTED` or `FAILED` result SHALL produce exactly one occurrence of its
matching code. Thus exact extraction code counts equal result counts under `IGNORE` or
`COLLECT`. If one failed hardlink source causes `N` hardlink results to fail under
`IGNORE` or `COLLECT`, the coordinator SHALL emit `N` ordered
`EXTRACTION_MEMBER_FAILED` occurrences, one naming each result member. Their contexts
SHALL share `failure_group_id` and `failure_group_size=N`. Under `RAISE`, the first
occurrence in result order escalates immediately; no completed report or `N`-occurrence
guarantee applies.

#### Scenario: user-filter skip is represented

- **WHEN** a selected member's user filter returns `None`
- **THEN** the report includes that member with `SKIPPED`, `path=None`, `error=None`, and no skip diagnostic

#### Scenario: selector exclusion is not a skip result

- **WHEN** a selector excludes a member before extraction
- **THEN** that member has no `ExtractionResult` and does not affect report result counts

#### Scenario: rejection and failure are distinct

- **WHEN** one member is blocked by `PathTraversalError` and another encounters a write `OSError`, both under `OnError.CONTINUE`
- **THEN** their results are respectively `REJECTED` and `FAILED`, with matching errors and diagnostic codes

#### Scenario: group hardlink failure counts results

- **WHEN** one failed source causes three hardlink members to receive `FAILED` results under `OnError.CONTINUE`
- **THEN** the report count for `EXTRACTION_MEMBER_FAILED` increases by three and the three retained contexts, if budget permits, share one failure-group id and size three

### Requirement: Error Policy (OnError) for extraction failures

`OnError.STOP` and `CONTINUE` SHALL retain their per-member meanings, but diagnostic
disposition composes as follows:

- Under `IGNORE` or `COLLECT`, `CONTINUE` records `REJECTED`/`FAILED` and proceeds.
- Under `RAISE`, emission raises `DiagnosticRaisedError`; this is an always-stop control
  exception and SHALL NOT be caught, converted to `REJECTED`/`FAILED`, or suppressed by
  `OnError.CONTINUE`.
- A diagnostic logging-handler or callback exception likewise propagates unchanged and is
  always-stop.
- Existing global resource guards, `KeyboardInterrupt`, `MemoryError`, and unexpected
  programming exceptions remain always-stop.

Under `OnError.STOP`, the original rejection/failure raises immediately. It SHALL not also
be converted to an extraction advisory, because the operation did not continue; genuine
exceptions remain the source of truth for fatal failures.

A per-member failure remains a `FilterRejectionError`, another member-scoped
`ArchiveyError`, or a filesystem `OSError` raised while reading/writing that member.
Under `CONTINUE`, partial output is removed before recording the result. A per-member
ratio violation remains continuable; cumulative bytes, archive-wide/live ratio, and
entry-count guards remain global always-stop limits. Exceptions outside the documented
`ArchiveyError` / per-member `OSError` set are never swallowed.

#### Scenario: collected continued failure returns in report

- **WHEN** a corrupt member fails under `OnError.CONTINUE` and default diagnostic policy
- **THEN** extraction logs/collects `EXTRACTION_MEMBER_FAILED`, records a `FAILED` result, and continues

#### Scenario: raised diagnostic defeats CONTINUE

- **WHEN** `EXTRACTION_MEMBER_FAILED` resolves to `RAISE` under `OnError.CONTINUE`
- **THEN** `DiagnosticRaisedError` carrying that occurrence halts extraction immediately and no report is returned

#### Scenario: STOP raises the genuine failure without advisory duplication

- **WHEN** a member raises `CorruptionError` under `OnError.STOP`
- **THEN** that `CorruptionError` propagates immediately and no continued-failure diagnostic is emitted

#### Scenario: filesystem error remains a continued member failure

- **WHEN** writing one member raises `OSError` under `OnError.CONTINUE`
- **THEN** partial output is removed, a `FAILED` result and `EXTRACTION_MEMBER_FAILED` occurrence are recorded, and extraction proceeds

#### Scenario: global resource guard still halts

- **WHEN** cumulative extracted bytes exceed their limit under `OnError.CONTINUE`
- **THEN** `ExtractionError` propagates immediately and no later member is processed

## ADDED Requirements

### Requirement: ExtractionReport is an immutable operation result

The system SHALL define:

```python
@dataclass(frozen=True)
class ExtractionReport:
    results: tuple[ExtractionResult, ...]
    diagnostics: DiagnosticSummary
```

The summary SHALL preserve exact operation counts after detail retention is exhausted.
The report SHALL not duplicate the cumulative reader collector or retain diagnostics
beyond the configured shared budget.

The report and each `ExtractionResult` freeze their structure, but this is not a deep
freeze. `ExtractionResult.member` is the original mutable, caller-read-only
`ArchiveMember`; its documented late-bound metadata and diagnostics MAY still change in
place. `error` may likewise refer to an ordinary exception object. The fixed result
outcomes and point-in-time `DiagnosticSummary` SHALL not change.

#### Scenario: report remains a point-in-time value

- **WHEN** a caller retains an extraction report and the reader later performs more work
- **THEN** the result tuple/outcome fields and diagnostic summary remain unchanged, although a referenced member may receive documented late-bound metadata
