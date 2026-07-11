# Design — lifecycle-aware diagnostics

## 1. Goals and constraints

Diagnostics are structured records for events where Archivey continues or deliberately
degrades. They are not replacements for genuine failures. The design must:

1. make every current warning machine-queryable;
2. put each event on a surface whose lifetime can actually contain it;
3. preserve exact counts without allowing retained details or attachments to become a
   memory-amplification vector;
4. support tolerant, streaming, logging-only, and strict callers with one deterministic
   policy;
5. remain JSON-safe and avoid secret leakage; and
6. keep the public surface small enough to support for the long term.

Pre-1.0 compatibility does not constrain the result. In particular, extraction changes
return type instead of adding a parallel helper or tuple mode.

## 2. Public values

The exact module layout is an implementation detail; these are the public shapes:

```python
class DiagnosticCode(str, Enum): ...
class DiagnosticSeverity(str, Enum):
    WARNING = "warning"

class DiagnosticDisposition(str, Enum):
    IGNORE = "ignore"
    COLLECT = "collect"
    RAISE = "raise"

@dataclass(frozen=True)
class Diagnostic:
    occurrence_id: str
    code: DiagnosticCode
    severity: DiagnosticSeverity
    message: str
    context: DiagnosticContext

@dataclass(frozen=True)
class DiagnosticSummary:
    total_count: int
    counts: Mapping[DiagnosticCode, int]
    retained: tuple[Diagnostic, ...]
    dropped_count: int

@dataclass(frozen=True)
class DiagnosticPolicy:
    default: DiagnosticDisposition = DiagnosticDisposition.COLLECT
    overrides: Mapping[DiagnosticCode, DiagnosticDisposition] = immutable_mapping()

@dataclass(frozen=True)
class ExtractionReport:
    results: tuple[ExtractionResult, ...]
    diagnostics: DiagnosticSummary
```

`DiagnosticCode`, `severity`, and every context discriminator are string enums so the
diagnostic serializes without a custom enum encoder. `DiagnosticSummary.counts` and policy
overrides are immutable mappings. Their constructors defensively copy caller mappings;
freezing only an outer dataclass while retaining a mutable dict is not sufficient.

Only `WARNING` is needed initially. The severity axis remains in the record so a later
informational taxonomy does not require changing the value shape, but policy matching is
per code only in this proposal.

### Typed, JSON-safe context

`Diagnostic.context` is a closed union of code-specific frozen context dataclasses, not a
`Mapping[str, object]`. Every variant has a literal string `kind` discriminator. This is
the complete initial code-to-context mapping:

| `DiagnosticCode` | Required context variant and fields |
|---|---|
| `MEMBER_NAME_NORMALIZED` | `NameNormalizationContext(kind="name_normalization", archive_name: str \| None, member_name: str, member_id: int \| None, raw_name_base64: str \| None, presented_name: str, normalized_name: str)` |
| `FORMAT_EXTENSION_CONFLICT` | `FormatConflictContext(kind="format_conflict", source_name: str \| None, extension: str \| None, extension_format: str, detected_format: str)` |
| `SCAN_DIRECTORY_VANISHED` | `ScanRaceContext(kind="scan_race", archive_name: str \| None, relative_path: str, entry_kind="directory")` |
| `SCAN_ENTRY_VANISHED` | `ScanRaceContext(kind="scan_race", archive_name: str \| None, relative_path: str, entry_kind="entry")` |
| `ARCHIVE_EOF_MARKER_MISSING` | `ArchiveEofContext(kind="archive_eof", archive_name: str \| None, format: str, expected_marker: str, expected_bytes: int, observed_bytes: int, observed_kind: str)` |
| `MEMBER_TIMESTAMP_INVALID` | `MemberTimestampContext(kind="member_timestamp", archive_name: str \| None, member_name: str, member_id: int \| None, field: str, source: str, value_repr: str)` |
| `SYMLINK_TARGET_UNAVAILABLE` | `SymlinkTargetContext(kind="symlink_target", archive_name: str \| None, member_name: str, member_id: int \| None, reason: str)` |
| `DIGEST_UNVERIFIABLE` | `DigestContext(kind="digest", archive_name: str \| None, member_name: str, member_id: int \| None, algorithm: str, reason: str)` |
| `SEEK_INDEX_DEGRADED` | `SeekIndexContext(kind="seek_index", archive_name: str \| None, member_name: str \| None, member_id: int \| None, codec: str, scan: str, error_type: str)` |
| `STREAM_REWIND_REDECOMPRESSES` | `StreamRewindContext(kind="stream_rewind", archive_name: str \| None, member_name: str \| None, member_id: int \| None, codec: str, from_offset: int, to_offset: int, accelerator: str \| None)` |
| `EXTRACTION_MEMBER_REJECTED` | `ExtractionOutcomeContext(kind="extraction_outcome", archive_name: str \| None, member_name: str, member_id: int \| None, status="rejected", error_type: str, failure_group_id: str \| None, failure_group_size: int \| None)` |
| `EXTRACTION_MEMBER_FAILED` | `ExtractionOutcomeContext(kind="extraction_outcome", archive_name: str \| None, member_name: str, member_id: int \| None, status="failed", error_type: str, failure_group_id: str \| None, failure_group_size: int \| None)` |

Accordingly, the closed alias is
`DiagnosticContext = NameNormalizationContext | FormatConflictContext |
ScanRaceContext | ArchiveEofContext | MemberTimestampContext |
SymlinkTargetContext | DigestContext | SeekIndexContext | StreamRewindContext |
ExtractionOutcomeContext`; backends cannot supply ad-hoc mapping variants.

`observed_kind` is a documented string enum with initial values `"absent"`,
`"short"`, and `"nonzero"`; `expected_marker` is a non-secret symbolic description
such as `"two_zero_blocks"`, never raw archive bytes. `source`, `scan`, `reason`, and
`error_type` similarly use documented non-secret string-enum/public-class-name values.
`member_id` is `None` only when emission necessarily precedes member registration.
`failure_group_id` and `failure_group_size` are both non-`None` only for results that
failed from one shared hardlink-source incident.

Every field is composed recursively only of `None`, `bool`, `int`, finite `float`, `str`,
and tuples of those values. Raw bytes use an explicitly named base64 string field when
lossless bytes are necessary. Exception objects, paths, arbitrary mappings, archive/member
objects, callbacks, and backend handles are prohibited. Each context and the containing
diagnostic provide a deterministic `to_dict()` whose result can be passed directly to
`json.dumps()`.

Messages and contexts MUST NOT include passwords, password candidates, values returned by
a `PasswordProvider`, encryption/decryption keys, key derivation material, or decrypted
header/payload bytes. A reason such as `"password_required"` and non-secret facts such as
an encrypted member's name are allowed. The same prohibition applies to the log projection
and `DiagnosticRaisedError`.

### Occurrence correlation, not identity

`occurrence_id` is opaque and unique for an emitted occurrence within the process. Copies
of a diagnostic retained in a summary and on an object compare by value and carry the same
id. The API promises neither `is` identity nor a stable id across runs. Ordering comes from
the tuple order in each summary, not from parsing occurrence ids.

## 3. Lifecycles and surfaces

### Detection

A standalone `detect_format()` call has one finite operation scope.
`FormatInfo.diagnostics` is its final `DiagnosticSummary`. A magic/extension conflict
attaches there; `ArchiveInfo` does not duplicate it.

`open_archive()` creates the prospective reader collector and its retention budget
**before** automatic detection, records a detection watermark, and passes that collector
into the internal detection routine. Successful backend construction transfers ownership
of the same collector to the reader. It does not seed, copy, merge, or replay diagnostics
into a second collector: detection and reader views address the same exact counters and
retained occurrence slots, and each occurrence consumes the budget once. The internal
`FormatInfo` has the point-in-time detection summary needed for backend selection; because
`open_archive()` does not return it, the library does not retain that snapshot after
handoff. If detection or open raises, the temporary collector is discarded after the
exception propagates.

An explicit `format=` override still creates the prospective reader collector before
backend open, but performs no detection and therefore creates no detection diagnostics.

### Reader and reader-owned streams

Each `ArchiveReader` owns one collector from successful creation until close.
`reader.diagnostics` returns a fresh immutable cumulative snapshot on every access:

- counts include every diagnostic emitted by detection/open/list/read/stream/extract work
  owned by that reader, including events no longer retained;
- retained occurrences are in emission order; and
- a previously returned snapshot never changes.

A reader-owned `ArchiveStream` exposes `stream.diagnostics`, an operation-filtered
snapshot over that same collector. The collector stores one aggregate occurrence; the two
views do not create separately retained copies. A standalone codec stream owns an
equivalent stream-lifetime collector.

Runtime events belong here. A slow rewind, failed optional seek-index scan, directory scan
race, and missing/trailing archive EOF event may happen long after open, so none is added
to frozen `CostReceipt` or `ArchiveInfo`. Those values continue to describe static
open-time properties only.

### Member and extraction attachment

Only an occurrence that is natural metadata about one concrete member is eligible for
object attachment:

- normalization, invalid timestamp, unavailable symlink target, or unverifiable digest
  may attach to `ArchiveMember.diagnostics`;
- format conflict attaches to `FormatInfo.diagnostics`.

Extraction rejection/failure already has structured `ExtractionResult.status` and
`.error`; duplicating it on a result diagnostics tuple would make that per-result API
budget-dependent without adding information, so `ExtractionResult` has no diagnostics
field. Extraction occurrences are retained only in the report/reader aggregate. Scan,
seek-index, rewind, and archive EOF occurrences are also aggregate-only. Member
attachments are read-only tuples on the otherwise live mutable member. A member's
`.replace()` copies its currently attached tuple by value; copies made by callers are
caller-owned and do not consume more library retention budget.

### Extraction scopes

`ArchiveReader.extract_all()` records a collector watermark before extraction and returns
an `ExtractionReport` whose summary is the exact count/retained delta for that call. It
does not include older reader occurrences; those remain visible through
`reader.diagnostics`.

Top-level `archivey.extract()` creates one operation collector and one report watermark
before detection. It passes that collector through internal detection, backend open, and
extraction; the temporary reader assumes ownership during the call, and extraction uses
the original top-level watermark rather than creating a separate report collector.
Consequently its report includes detection, open, read, and extraction diagnostics caused
by the call with one counter set, one occurrence order, and one retention budget. No
phase copies or re-retains an occurrence.

At successful return, the report receives a bounded point-in-time summary and the
temporary reader is closed; the returned values are caller-owned. If extraction halts by
exception, no report is returned. On a caller-owned reader, the cumulative reader snapshot
still exposes occurrences emitted before the halt; `DiagnosticRaisedError` also carries
the escalated diagnostic.

`ExtractionReport` is frozen and contains an immutable result tuple and immutable
diagnostic snapshot. Each `ExtractionResult` is also frozen, so its `path`, `status`, and
`error` reference cannot be replaced after return. This is **not** a deep-frozen archive
snapshot: `ExtractionResult.member` refers to the original mutable, caller-read-only
`ArchiveMember`, whose documented late-bound metadata and member diagnostics may still be
filled in place. The report promises a point-in-time diagnostic summary and fixed outcome
structure, not frozen member metadata or exception internals.

## 4. One shared retention budget

`ArchiveyConfig.max_retained_diagnostic_references` is a non-negative integer (default
256). A standalone detection, reader lifetime, top-level extraction operation, or
standalone stream owns one budget. The budget limits **all diagnostic references retained
by the library for that collector**:

1. each occurrence stored in the aggregate retained tuple consumes one slot; and
2. each eligible `ArchiveMember` attachment consumes one additional slot.

On emission, the collector allocates in a fixed order: aggregate slot first, then the one
most-specific eligible attachment. An attachment is made only when the aggregate
occurrence was retained. If insufficient slots remain, detail/attachment is omitted.
Thus a member-specific occurrence normally uses two slots, while an aggregate-only event
uses one. No extraction occurrence attaches to a result.

Exact `total_count` and per-code counts increment for every event even after the budget is
exhausted and even under `IGNORE`. They are integer counters, not retained references.
`dropped_count == total_count - len(retained)` counts aggregate details not retained; it
does not count omitted attachment slots.

Snapshots contain at most the configured number of aggregate details and are not cached.
A caller may retain snapshots or make member copies; that memory is caller-owned and
outside the library's retention guarantee.

Collector watermarks are opaque internal sequence/counter positions. Creating a watermark
does not copy diagnostics or consume retention slots. A summary for a watermark range
computes exact count deltas and filters the collector's already-retained tuple by
occurrence sequence; the snapshot does not cause the library to re-retain occurrences.

## 5. Policy and emission order

The default disposition is `COLLECT`. Per-code overrides are the only matching dimension.
The complete matrix is:

| Disposition | Exact counts | Aggregate detail | Eligible attachment | WARNING log | Callback | Exception |
|---|---:|---:|---:|---:|---:|---|
| `IGNORE` | yes | no | no | no | no | none |
| `COLLECT` | yes | budget permitting | budget permitting | yes | yes, if configured | none |
| `RAISE` | yes | budget permitting | budget permitting | yes | yes, if configured | `DiagnosticRaisedError` |

For each event the emitter performs these steps:

1. validate the code, severity, message, and typed secret-free context payload;
2. resolve disposition;
3. under the collector's internal lock, allocate the occurrence id, construct the
   immutable diagnostic, increment the one collector's exact counters, and reserve
   aggregate/attachment slots in emission order;
4. release the lock;
5. for `COLLECT`/`RAISE`, emit the WARNING log projection;
6. for `COLLECT`/`RAISE`, invoke `on_diagnostic(diagnostic)` on the calling thread; and
7. for `RAISE`, if the callback returned normally, raise
   `DiagnosticRaisedError(diagnostic)`.

Logging handlers and callbacks are application code. Neither is invoked while an internal
collector, reader, stream, backend, or registry lock is held. State is updated before
either, so a callback may read `reader.diagnostics` and observe the current occurrence.
Callbacks are synchronous and receive events in emission order.

If logging itself raises because an application installed a failing handler, normal
Python logging semantics apply: that exception propagates and the callback/escalation
steps do not run. If `on_diagnostic` raises, its exception propagates unchanged; it is not
wrapped, not converted into an extraction result, and is not governed by
`OnError.CONTINUE`. The occurrence has already been counted/retained/logged. Under
`RAISE`, the callback exception takes precedence over `DiagnosticRaisedError`, but the
operation still halts.

The callback may inspect immutable arguments and read snapshot properties. It MUST NOT
drive another operation on the same reader/stream while that operation is emitting.
Archivey detects same-emitter operational reentrancy and raises
`UnsupportedOperationError` rather than deadlocking or recursively corrupting ordering.
Using another reader is allowed. Snapshot access itself is explicitly non-reentrant and
allowed.

## 6. Escalation and specialized strictness

`DiagnosticRaisedError` is a direct `ArchiveyError` subtype carrying a required
`diagnostic: Diagnostic`. Standard format/archive/member context stamping still applies.
It wraps no underlying exception merely because a diagnostic was escalated.

It is an always-stop control exception. Extraction MUST NOT catch it as a per-member
failure under `OnError.CONTINUE`, and it never becomes `FAILED` or `REJECTED`.

`ArchiveyConfig.strict_archive_eof` remains the specialized contract for a missing archive
EOF marker. The event is counted and processed under the matrix first. If
`strict_archive_eof=True`, `TruncatedError` takes precedence over a `RAISE` disposition:

- `IGNORE` counts then raises `TruncatedError`;
- `COLLECT` counts/retains/logs/calls back then raises `TruncatedError`; and
- `RAISE` does the same delivery but raises `TruncatedError`, not
  `DiagnosticRaisedError`.

A logging/callback exception still propagates at its ordered step before either terminal
error. With `strict_archive_eof=False`, the ordinary policy applies, so `RAISE` yields
`DiagnosticRaisedError`.

## 7. Extraction result semantics

`ExtractionReport.results` contains one result for every selected member processed by a
successfully completed extraction operation. This includes a member rejected by
universal/policy safety checks before the user filter runs:

| Status | Meaning | `path` | `error` |
|---|---|---|---|
| `EXTRACTED` | destination entry successfully created | created path | `None` |
| `SKIPPED` | intentionally not written: user filter returned `None` or `OverwritePolicy.SKIP` found an existing destination | `None` | `None` |
| `REJECTED` | a `FilterRejectionError` blocked the member under `OnError.CONTINUE` | `None` | that error |
| `FAILED` | another per-member `ArchiveyError` or allowed filesystem `OSError` failed under `OnError.CONTINUE` | `None` | that error |

Selector-excluded members are outside the operation and have no result. `SKIPPED` is not a
failure and does not itself create a diagnostic. Under `OnError.STOP`, a rejection or
failure raises immediately, so no report is returned. Under `CONTINUE`, rejection/failure
occurrences follow diagnostic policy: default `COLLECT` logs and records them; `IGNORE`
still produces the correct result and count but no detail/log/callback; `RAISE` halts
instead of returning that result as recoverable.

The count unit for `EXTRACTION_MEMBER_REJECTED` and `EXTRACTION_MEMBER_FAILED` is one
continued `ExtractionResult` with the corresponding status, not one root-cause incident
or one warning call site. Therefore exact per-code counts equal the number of returned
`REJECTED`/`FAILED` results under `IGNORE` or `COLLECT`. If one failed hardlink source
causes `N` hardlink results to fail and disposition permits continuation (`IGNORE` or
`COLLECT`), the coordinator emits `N` ordered `EXTRACTION_MEMBER_FAILED` occurrences.
Their contexts carry the same opaque `failure_group_id` and `failure_group_size=N`, while
each occurrence names its own failed result member. Under `RAISE`, the first occurrence in
result order is counted/delivered and escalates immediately, so no completed report or
promise of `N` emitted occurrences exists. This preserves per-result counting without
retaining exception objects.

## 8. Initial taxonomy: all 17 current warning calls

Multiple call sites may intentionally share a code when their machine meaning is the same;
typed context supplies the variant.

| # | Current site | Stable code | Context / attachment |
|---:|---|---|---|
| 1 | `internal/streams/xz.py`: per-stream backward index scan failed | `SEEK_INDEX_DEGRADED` | codec=`xz`, scan=`per_stream`; stream/reader aggregate |
| 2 | `internal/streams/verify.py`: digest algorithm unavailable | `DIGEST_UNVERIFIABLE` | algorithm + member; member eligible |
| 3 | `internal/naming.py`: presented name normalized | `MEMBER_NAME_NORMALIZED` | stored/decoded + normalized name; member eligible |
| 4 | `internal/streams/decompressor_stream.py`: backward index/trailer scan failed | `SEEK_INDEX_DEGRADED` | codec + scan kind + public error type; stream/reader aggregate |
| 5 | `internal/streams/archive_stream.py`: rewind without available accelerator | `STREAM_REWIND_REDECOMPRESSES` | codec + offsets + accelerator; stream/reader aggregate |
| 6 | `internal/streams/archive_stream.py`: rewind for codec with no index | `STREAM_REWIND_REDECOMPRESSES` | codec + offsets, accelerator=`None`; stream/reader aggregate |
| 7 | `internal/extraction.py`: continued member rejection/failure | `EXTRACTION_MEMBER_REJECTED` or `EXTRACTION_MEMBER_FAILED` | one aggregate occurrence per corresponding result |
| 8 | `internal/extraction.py`: orphaned hardlink source failed | `EXTRACTION_MEMBER_FAILED` | one occurrence per failed hardlink result; shared failure-group fields correlate them |
| 9 | `internal/extraction.py`: individual hardlink failed | `EXTRACTION_MEMBER_FAILED` | one aggregate occurrence for that failed result |
| 10 | `internal/backends/directory_reader.py`: directory vanished during scan | `SCAN_DIRECTORY_VANISHED` | relative directory; reader aggregate |
| 11 | `internal/backends/directory_reader.py`: entry vanished during scan | `SCAN_ENTRY_VANISHED` | relative entry; reader aggregate |
| 12 | `internal/backends/tar_reader.py`: missing/invalid TAR EOF marker | `ARCHIVE_EOF_MARKER_MISSING` | expected marker + observed length/type; reader aggregate |
| 13 | `internal/backends/tar_reader.py`: invalid TAR mtime | `MEMBER_TIMESTAMP_INVALID` | member + field/source/value representation; member eligible |
| 14 | `internal/backends/zip_reader.py`: invalid NTFS FILETIME | `MEMBER_TIMESTAMP_INVALID` | member + NTFS field + value; member eligible |
| 15 | `internal/backends/zip_reader.py`: invalid ZIP DOS `date_time` | `MEMBER_TIMESTAMP_INVALID` | member + DOS field + value; member eligible |
| 16 | `internal/backends/zip_reader.py`: encrypted symlink target unavailable | `SYMLINK_TARGET_UNAVAILABLE` | member + reason=`password_required`; member eligible |
| 17 | `internal/detection.py`: extension conflicts with content | `FORMAT_EXTENSION_CONFLICT` | source + suggested/detected format; `FormatInfo` |

The migration preserves the logger hierarchy and severity, but logging strings are the
human projection of these records rather than a separate compatibility contract.

## 9. Sequencing

This capability changes the public reader, member, stream, extraction, config, and error
surfaces. It is therefore a Phase 5 public-API follow-on and must land before Phase 6
native 7z/RAR readers, whose parsers would otherwise introduce new raw warning paths that
immediately need migration. The specs in this proposal land first; implementation follows
only when the 11 tasks are explicitly started.
