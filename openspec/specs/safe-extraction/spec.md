# Safe Extraction

## Purpose

Safe extraction writes archive members to a destination directory while enforcing
non-bypassable path safety, link safety, overwrite rules, permission transforms,
decompression-bomb limits, progress callbacks, diagnostics, and per-member
results. It is the caller-facing path for putting archive contents on disk.

## Related specs

| Spec | Relationship |
| --- | --- |
| `archive-reading` | `open_archive()`, `ArchiveReader`, selectors, reader diagnostics, access methods |
| `access-mode-and-cost` | `extract_all()` as a forward-pass method and streaming legality |
| `diagnostics` | Diagnostic values, retention budgets, watermarks, extraction outcome codes |
| `error-handling` | Exception classes and ordered diagnostic/exception behavior |
| `format-tar` | TAR hardlink ordering, link recovery, and TAR-specific extraction constraints |

## Requirements

### Requirement: One-Shot Extraction API

The top-level API SHALL expose one-shot extraction and return an immutable
`ExtractionReport` on success:

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

The call SHALL extract all members, deliberately has no `members=` selector, and
uses the same source/password/encoding/config precedence, default `STRICT`
policy, default `ERROR` overwrite policy, and automatic streaming mode for
non-seekable supported sources as the reader APIs. Subset extraction goes through
`ArchiveReader.extract_all()`.

The call SHALL use one diagnostic collector and one retention budget for
detection, backend open, reading, and extraction. The final report uses the
reader collector's cumulative snapshot/range; phases do not seed, copy, merge, or
re-retain events. If an always-stop condition or `OnError.STOP` raises, no report
is returned.

#### Scenario: one-shot extraction matrix

| Case | Expected |
| --- | --- |
| `archivey.extract(source, dest)` completes | Returns `ExtractionReport(results=(...), diagnostics=...)` with all detection/open/read/extraction diagnostics from that call |
| Detection emits one retained conflict and extraction emits one retained failure | One occurrence order and one budget from before detection; no duplicated phase handoff events |
| Non-seekable supported source | Opens in streaming mode automatically and extracts in one forward pass |
| Caller wants only some members | Caller opens the archive and calls `reader.extract_all(dest, members=...)`; top-level `extract()` has no selection parameter |
| `encoding="cp932"` for a TAR with CP932 names | Disk paths match `open_archive(..., encoding="cp932")` followed by `extract_all()` |

### Requirement: Per-Reader Extract-All Helper

`ArchiveReader.extract_all()` SHALL expose per-reader extraction with optional
member selection and filtering:

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

The helper SHALL record a diagnostic watermark at call start and return a report
whose summary contains exact count/retained deltas for this extraction call only.
`reader.diagnostics` remains cumulative. A per-call config may change new-event
policy/callback behavior but MUST use the reader's existing collector and
retention maximum.

Selection, filter ordering, one-pass selected extraction, reader-config
inheritance, and per-call limits precedence retain their existing contracts.
There is no single-member `reader.extract()` method.

#### Scenario: extract_all matrix

| Case | Expected |
| --- | --- |
| Reader emitted a diagnostic before `extract_all()` and another during extraction | Report summary includes only the extraction occurrence; `reader.diagnostics` includes both |
| `reader.extract_all(dest, members=["a", "b"])` on a solid archive | Only selected members are extracted in one decompression pass |
| Caller wants one file | Uses `reader.extract_all(dest, members=[name])`; no separate single-member API |
| `extract_all(config=...)` overrides diagnostic policy/callback | New extraction events use the override while retention remains under the reader's original budget |

### Requirement: Extraction reads limits and strictness from the configuration object

`archivey.extract()` and `ArchiveReader.extract_all()` SHALL accept both
`config: ArchiveyConfig | None` and `limits: ExtractionLimits | None`. Per-call
`limits` takes precedence over `config.extraction_limits`, then the
reader/library default. `ExtractionLimits.UNLIMITED` disables byte, ratio,
archive-wide ratio/live-ratio, and entry-count guards. Policy, overwrite,
`on_error`, progress, and member-selection/filter arguments remain operational
arguments outside config.

Top-level `extract()` SHALL use the supplied config for its one collector.
`extract_all()` uses the reader config by default; an explicit config affects
new-event policy/callback behavior but not the existing collector or retention
maximum. Both APIs always return `ExtractionReport` with an accumulated immutable
result tuple on success; there is no no-tracking mode.

#### Scenario: limits/config matrix

| Case | Expected |
| --- | --- |
| `extract_all(limits=...)` on an existing reader | Limits apply to this extraction; report remains a watermark range over the existing collector |
| `extract(..., config=ArchiveyConfig(extraction_limits=ExtractionLimits(max_extracted_bytes=10 * 2**30)))` | Cumulative byte limit is 10 GiB |
| Reader config has limits, call passes `limits=ExtractionLimits(max_extracted_bytes=50 * 2**20)` | 50 MiB governs this run; later calls without `limits` revert to reader config |
| `limits=ExtractionLimits.UNLIMITED` | Archives that would trip default guards complete without bomb-guard error |
| Reader opened with custom config and `extract_all(dest)` | Reader config, including extraction limits, governs the run |

### Requirement: Non-Bypassable Universal Path-Safety Constraints

The system SHALL run universal safety checks on the faithful stored
`member.name` before any policy transform, user filter, or filesystem write;
`ExtractionPolicy.TRUSTED` does not bypass them. The default path-safety behavior
is reject/raise. A future sanitize policy is outside v1 scope and is not part of
this contract.

The implementation SHALL enforce defense in depth: first a string check rejects
absolute paths, Windows drive/UNC roots, any `..` component split on `/` or `\`,
and null bytes; then `(dest / member.name).parent.resolve()` must remain within
`dest.resolve()` to catch symlinked intermediate components without following a
final-component symlink; link targets are rechecked as described in the symlink
and hardlink requirements.

| Constraint | Violation type | Condition |
| --- | --- | --- |
| Path traversal | `PathTraversalError` | Any `..` component, escaping or internal |
| Absolute path | `PathTraversalError` | Leading `/`, Windows drive path, or UNC path |
| Null byte | `PathTraversalError` | `member.name` contains `\x00` |
| Symlink escape | `SymlinkEscapeError` | SYMLINK whose fully resolved target escapes `dest` |
| Hardlink escape | `SymlinkEscapeError` | HARDLINK whose target path resolves outside `dest` |
| Special file | `SpecialFileError` | `MemberType.OTHER` device/FIFO/socket/etc. |

#### Scenario: universal safety matrix

| Case | Expected |
| --- | --- |
| `"../evil"` or `"../../etc/passwd"` | `PathTraversalError`; no write; all policies |
| `"foo/../bar"` | `PathTraversalError` under reject/raise behavior even if it would stay in root |
| Leading `/`, Windows drive, UNC path | `PathTraversalError`; no write; all policies |
| Earlier member creates symlink `foo` outside `dest`; later member writes `foo/x` | Parent resolution rejects `foo/x` with `PathTraversalError` |
| `MemberType.OTHER` | `SpecialFileError`; all policies |

### Requirement: Symlink Escape Re-Validated at Extraction Time

The system SHALL validate a SYMLINK member after `os.symlink(link_target,
dest_path)` creates the link on disk. It resolves the created link target with
`Path.resolve()` and, if the resolved path escapes `dest`, immediately unlinks the
new link and raises `SymlinkEscapeError`. Resolution failures from symlink loops
or platform equivalents (`OSError` such as `ELOOP`, or `RuntimeError`) SHALL fail
safe the same way: unlink the just-created link and reject the member.

This post-creation check SHALL catch chained symlink attacks where earlier archive
members influence later target resolution, without allowing writes through an
escaping link.

#### Scenario: symlink revalidation matrix

| Case | Expected |
| --- | --- |
| Created symlink resolves outside `dest` | Link is unlinked; `SymlinkEscapeError`; no later data written through it |
| Chained symlink attack through earlier member | Post-creation resolution catches the escape and raises `SymlinkEscapeError` |
| Cyclic links (`a -> b`, `b -> a`) make `Path.resolve()` raise | Just-created link is unlinked; `SymlinkEscapeError`; no uncaught OS/runtime error |

### Requirement: Hardlink Two-Pass Extraction

The system SHALL support TAR-style hardlinks through the extraction coordinator as
a pull-based sink over reader streams. Ordinary FILE/DIR/SYMLINK members are
written as reached; each written FILE path is recorded under its source. A
HARDLINK whose source already has recorded paths tries `os.link()` against them
in order; if all fail with cross-device `EXDEV`, the coordinator falls back to
`shutil.copy2()` and records the copy for later links on that device.

When a selected HARDLINK's source was excluded by `members` or `filter`, the
system MUST NOT materialize the excluded source at its own destination. It SHALL
make the source content available only through selected link destinations: write
the bytes to the first selected link path allowed by `OverwritePolicy`, record
`SKIPPED` links under `SKIP`, link further selected links to the materialized
path, and write nothing if every selected link is skipped. The materialized file
gets the selected link's transformed metadata. An equivalent hidden temp inside
`dest` is permitted.

The coordinator SHALL avoid wasted passes: if a free member list exists
(`get_members_if_available()`), recovery is planned in one forward pass; otherwise
a seekable source may use one conditional second pass; a forward-only source makes
the orphaned link unrecoverable and therefore a per-member failure governed by
`OnError`. A hardlink that merely precedes its selected source is linked after the
source is written, with one read and one bomb-limit count for the source bytes.

#### Scenario: hardlink matrix

| Case | Expected |
| --- | --- |
| HARDLINK reached after its source was extracted | Try `os.link()` against recorded source paths; fallback to copy on all-`EXDEV` |
| Selected hardlink source was excluded but recoverable | Source content appears at selected link path(s); excluded source path is never created |
| First selected link destination exists under `OverwritePolicy.SKIP` | That link result is `SKIPPED`; content moves to the next allowed link; all skipped means no write |
| Excluded source on a forward-only stream | Per-member failure: `STOP` raises; `CONTINUE` records `FAILED` and proceeds |
| HARDLINK appears before its also-selected source | After the pass it links to the extracted source inode; source bytes read and counted once |

### Requirement: Policy-Specific Metadata Transforms

The system SHALL apply policy-specific permission and ownership transforms to one
transient `ArchiveMember` copy after universal checks pass and before I/O. The
copy receives the policy transform and user `filter` in that order and supplies
the on-disk identity (`name`, mode, timestamps, destination path). The original
mutable member is used for `BombTracker.start_member()` and recorded in
`ExtractionResult`, so late-bound size/CRC/source metadata remain accurate.

```python
class ExtractionPolicy(Enum):
    STRICT = "strict"
    STANDARD = "standard"
    TRUSTED = "trusted"
```

Policies SHALL parallel Python `tarfile`'s `data` / `tar` / `fully_trusted`
mental model while applying uniformly to all formats and retaining Archivey's
non-bypassable safety checks.

| Behavior | `STRICT` default | `STANDARD` | `TRUSTED` |
| --- | --- | --- | --- |
| Path, absolute-path, link-escape, special-file rejection | Always | Always | Always |
| Missing file/dir mode | File `0o644`, dir `0o755` | File `0o644`, dir `0o755` | Apply as stored |
| Permission normalization | Files max `0o644`; dirs `0o755`; strip file execute | Preserve ordinary execute bits | Apply as stored |
| setuid/setgid/sticky | Strip all | Strip setuid/setgid | Preserve |
| uid/gid | Strip | Strip | Apply only when running as root; otherwise skip silently |

#### Scenario: metadata policy matrix

| Case | Expected |
| --- | --- |
| FILE `mode=0o755` under `STRICT` | Written as `0o644` |
| FILE `mode=0o755` under `STANDARD` | Execute bits preserved; setuid/setgid stripped |
| FILE with uid/gid under `TRUSTED` as root | uid/gid applied |
| Any policy, unsafe path/link/special file | Universal safety rejection still applies |

### Requirement: Overwrite Policy

The system SHALL enforce `OverwritePolicy` whenever a destination entry already
exists at the transformed member path:

```python
class OverwritePolicy(Enum):
    ERROR = "error"
    SKIP = "skip"
    REPLACE = "replace"
```

`ERROR` raises a per-member `ExtractionError` governed by `OnError`; `SKIP`
records a `SKIPPED` result and is not a failure. Existence checks SHALL use
`lstat` semantics so dangling symlinks count as existing entries. `REPLACE` SHALL
be atomic and never write through a symlink: FILE data streams to a temp file in
the destination directory, metadata is applied, and `os.replace()` moves it onto
the destination. A mid-stream failure preserves the old entry and discards only
the temp. DIR/SYMLINK/HARDLINK replacement removes the existing entry and creates
fresh; replacing an existing directory with a file removes the directory first.

#### Scenario: overwrite matrix

| Case | Expected |
| --- | --- |
| Existing path under `ERROR` | `ExtractionError`; existing entry unmodified |
| Existing path under `SKIP` | `ExtractionResult.status == SKIPPED`, `path=None`, no exception |
| Existing file under `REPLACE` | Fresh file is written via temp file + `os.replace()` |
| Existing symlink under `REPLACE` | Symlink entry itself is replaced; bytes never follow the old link |
| `REPLACE` fails mid-stream | Existing file remains unchanged; temp is discarded |
| Dangling symlink under `ERROR` or `SKIP` | Treated as existing; no write-through to target |

### Requirement: Extraction as a Composable Module

The system SHALL implement safe extraction in a dedicated coordinator module
separate from reader backends and format detection. Both `archivey.extract()` and
`ArchiveReader.extract_all()` delegate to the same `ExtractionCoordinator`, which
drives one unified forward pass over `(member, stream)` pairs in streaming and
random-access modes.

The coordinator SHALL own member selection, transient metadata transforms, user
filter application, `BombTracker` calls, progress callbacks, result accumulation,
and extraction diagnostics. Reader generators yield original mutable members so
backend late-bound updates remain visible; copy-producing transforms/filters do
not detach streamed members from backend updates.

#### Scenario: coordinator matrix

| Case | Expected |
| --- | --- |
| `extract_all()` in random-access mode | Uses `ExtractionCoordinator.run()` forward pass |
| `extract_all()` in streaming mode | Uses the same coordinator pass and consumes the streaming pass per `access-mode-and-cost` |
| Backend fills late-bound fields while streaming | Original member in `ExtractionResult` and `BombTracker` sees the final source metadata |

### Requirement: Enforce Cumulative Max-Extracted-Bytes Limit

The system SHALL track total bytes written across a single `extract()` or
`extract_all()` call and raise `ExtractionError` at the chunk boundary where the
total exceeds `max_extracted_bytes`. The default is 2 GiB
(2,147,483,648 bytes). Callers override it through `ExtractionLimits`; `None` via
`ExtractionLimits.UNLIMITED` disables this guard.

The limit SHALL be tracked by one `BombTracker` per extraction call. It is a
global resource guard: when it trips, extraction halts and no later members are
processed regardless of `OnError`.

#### Scenario: cumulative byte limit matrix

| Case | Expected |
| --- | --- |
| Running written-byte total crosses `max_extracted_bytes` | Immediate `ExtractionError`; extraction halts |
| `ExtractionLimits(max_extracted_bytes=10 * 2**30)` | Enforced cumulative limit is 10 GiB |
| `ExtractionLimits.UNLIMITED` | Cumulative byte guard is disabled |

### Requirement: Enforce Per-ArchiveMember Max Decompression Ratio

The system SHALL raise `ExtractionError` when a single member's output exceeds
`max_ratio * member.compressed_size` after that member's output crosses
`ratio_activation_threshold`. Defaults are `max_ratio=1000.0` and threshold
5 MiB. The ratio is per-member output, not cumulative output, and is checked only
when `member.compressed_size` is known and greater than zero. The original member
is used so late-bound compressed-size metadata remains accurate.

This guard SHALL be independent of cumulative bytes and archive-wide ratio
guards. A ratio violation for one member is member-scoped and may be continued
under `OnError.CONTINUE`; global guards remain always-stop.

#### Scenario: per-member ratio matrix

| Case | Expected |
| --- | --- |
| Member output exceeds ratio after threshold | `ExtractionError` while processing that member |
| Tiny highly-compressible member stays below threshold | No ratio error; threshold prevents false positive |
| `compressed_size is None` or `0` | Per-member ratio skipped; cumulative/global guards still apply |
| `ExtractionLimits(max_ratio=100)` | Members over 100:1 trip this guard |

### Requirement: Bomb Protection Scope Limited to Extraction Paths

The system SHALL apply extraction bomb limits only during `archivey.extract()` and
`ArchiveReader.extract_all()`. `ArchiveReader.read()` and `ArchiveReader.open()`
return decompressed data/streams without byte, ratio, or entry-count enforcement;
callers are responsible for guarding direct reads.

#### Scenario: bomb-scope matrix

| Case | Expected |
| --- | --- |
| `reader.read(member)` on extreme-ratio data | Raw decompressed bytes returned or normal read error; no extraction bomb guard |
| `reader.open(member)` | Stream delivers decompressed data without extraction limits |

### Requirement: Progress Reporting via on_progress Callback

The system SHALL accept optional `on_progress` callbacks on both extraction APIs
and call the callback once per processed member with `ExtractionProgress`:

```python
@dataclass
class ExtractionProgress:
    member: ArchiveMember
    bytes_written: int
    total_bytes_estimated: int | None
    members_done: int
    members_total: int | None
```

`bytes_written` is cumulative for the operation. `total_bytes_estimated` is
`None` when the format lacks uncompressed size information; `members_total` is
`None` when the attempted member count cannot be known without a scan. When a
free member list exists and a `members` selector is provided, totals SHALL cover
only selected members. `members_done` counts every selected member processed,
including user-filter skips and failures, so it reaches `members_total`;
selector-excluded members are invisible. Predicate selectors evaluated against an
upfront index MUST be pure functions of the member.

#### Scenario: progress matrix

| Case | Expected |
| --- | --- |
| `extract(..., on_progress=cb)` | `cb` called once per processed member with cumulative bytes and counters |
| Format cannot provide uncompressed sizes | `total_bytes_estimated is None` |
| Free list + selector | Totals cover selected members only; filter skips/failures still advance `members_done` |

### Requirement: Per-ArchiveMember ExtractionResult with Status

`ExtractionReport.results` SHALL contain one `ExtractionResult` for every
selected member the coordinator processes when the operation completes, including
members rejected by universal/policy checks before the user filter. Selector
exclusions are outside the operation and have no result.

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

Statuses SHALL mean: `EXTRACTED` created an entry (`path` set, `error=None`);
`SKIPPED` intentionally bypassed writing because the user filter returned `None`
or `OverwritePolicy.SKIP` found an existing destination (`path=None`,
`error=None`); `REJECTED` is a continued `FilterRejectionError`; `FAILED` is a
continued non-rejection per-member `ArchiveyError` or permitted filesystem
`OSError`. `SKIPPED` is not a failure and emits no diagnostic.

Continued `REJECTED`/`FAILED` results SHALL emit exactly one matching
`EXTRACTION_MEMBER_REJECTED` / `EXTRACTION_MEMBER_FAILED` occurrence per result.
`ExtractionResult` has no diagnostics field; `status` and `error` are the
per-result outcome while diagnostics live in the report/reader aggregate. If one
failed hardlink source causes `N` hardlink failures under `IGNORE` or `COLLECT`,
the coordinator emits `N` ordered `EXTRACTION_MEMBER_FAILED` occurrences with
shared `failure_group_id` and `failure_group_size=N`; under `RAISE`, the first
ordered occurrence escalates immediately and no completed report/count guarantee
applies.

#### Scenario: result/status matrix

| Case | Expected |
| --- | --- |
| User filter returns `None` | Result is `SKIPPED`, `path=None`, `error=None`, no skip diagnostic |
| Selector excludes member | No `ExtractionResult`; no result-count impact |
| Member blocked by `PathTraversalError` under `CONTINUE` | Result is `REJECTED` with matching error and diagnostic |
| Member write raises `OSError` under `CONTINUE` | Result is `FAILED` with matching error and diagnostic |
| Member written successfully | Result is `EXTRACTED`, `path` points to created entry |
| Existing destination under `OverwritePolicy.SKIP` | Result is `SKIPPED`, `path=None` |
| One failed source causes three hardlink results to fail | Failed count increases by three; retained contexts, if budget permits, share one failure group id/size |

### Requirement: Error Policy (OnError) for extraction failures

`OnError.STOP` and `OnError.CONTINUE` SHALL govern per-member failures only.
Under `CONTINUE`, a member-scoped `FilterRejectionError`, other member-scoped
`ArchiveyError`, permitted read/write `OSError`, or per-member ratio violation
records `REJECTED`/`FAILED`, removes partial output, emits the matching diagnostic
under the active diagnostic policy, and proceeds.

Diagnostic disposition SHALL still be authoritative: `RAISE` emits
`DiagnosticRaisedError` and halts immediately even under `OnError.CONTINUE`;
logging-handler and diagnostic-callback exceptions propagate unchanged. Under
`STOP`, the genuine rejection/failure raises immediately and is not converted to
an extraction advisory. Global resource guards, `KeyboardInterrupt`,
`MemoryError`, and unexpected programming exceptions are always-stop and are not
swallowed.

#### Scenario: OnError matrix

| Case | Expected |
| --- | --- |
| Corrupt member under `CONTINUE` and default diagnostics | Partial output removed; `FAILED` result; `EXTRACTION_MEMBER_FAILED`; later members continue |
| Extraction diagnostic resolves to `RAISE` under `CONTINUE` | `DiagnosticRaisedError` halts; no report returned |
| `CorruptionError` under `STOP` | Original `CorruptionError` propagates; no continued-failure diagnostic |
| Filesystem `OSError` while writing under `CONTINUE` | Partial output removed; `FAILED` result/diagnostic; extraction proceeds |
| Cumulative bytes/live ratio/max entries exceed limit under `CONTINUE` | `ExtractionError` propagates and halts; no later member processed |
| Default `STOP` member failure | Exception raises immediately; failing partial file removed; earlier outputs remain |
| Mixed good/corrupt archive under `CONTINUE` | Extractable members are written; report includes `EXTRACTED` plus `FAILED`/`REJECTED`; no per-member exception escapes |

### Requirement: ExtractionReport is an immutable operation result

The system SHALL define:

```python
@dataclass(frozen=True)
class ExtractionReport:
    results: tuple[ExtractionResult, ...]
    diagnostics: DiagnosticSummary
```

The report SHALL preserve fixed result outcomes and a point-in-time diagnostic
summary with exact operation counts after retention is exhausted. It does not
duplicate the cumulative reader collector or retain beyond the shared budget.
Immutability is structural, not deep: `ExtractionResult.member` is the original
mutable, caller-read-only `ArchiveMember`, whose documented late-bound metadata
and diagnostics may still change; `error` may be an ordinary exception object.

#### Scenario: report immutability matrix

| Case | Expected |
| --- | --- |
| Caller keeps a report and reader later does more work | Result tuple/outcomes and diagnostic summary stay unchanged; referenced member may receive documented late-bound updates |

### Requirement: Archive-wide decompression ratio for solid containers

The system SHALL evaluate a static archive-wide ratio during extraction when a
member's `compressed_size` is unknown/zero and the reader exposes a cheap
`compressed_source_size`. The denominator is the archive source byte size: path
`stat`, trusted integer `size`, `try_get_size()` from Archivey streams, or an
O(1)-safe `SEEK_END`/restore probe for real files, `BytesIO`, and `mmap`.
Anything that would decompress or scan payload to answer (for example foreign
decompressor streams) yields `None`. For compressed containers this is compressed
size; for uncompressed containers the resulting ratio is about 1:1 and harmless.

The ratio SHALL be `cumulative_bytes_written / compressed_source_size`, checked
in `BombTracker.count()` using the same `max_ratio` and cumulative
`ratio_activation_threshold` as other ratio guards. If `compressed_source_size`
is absent, the static archive-wide check is skipped. Per-member and archive-wide
ratios are independent; either may trip first.

#### Scenario: static archive-wide ratio matrix

| Case | Expected |
| --- | --- |
| Small `.tar.gz` file with known source size expands past `max_ratio` after threshold | `ExtractionError` during extraction |
| Compressed tar from non-seekable pipe with unknown size | Static archive-wide ratio skipped; cumulative byte limit still applies |
| Plain `.tar` | No meaningful compressed denominator; archive-wide ratio does not trip |
| ZIP member has known `compressed_size` | Per-member ratio applies; archive-wide ratio does not replace it |
| Nested archive opened from an Archivey member/codec stream with cheap size | Cheap source size may serve as archive-wide denominator |

### Requirement: Enforce Maximum Entry Count

The system SHALL count members actually written to disk during one extraction call
and raise `ExtractionError` once the count exceeds `max_entries`. The default is
`1_048_576`; callers override through `ExtractionLimits`, and `None` disables the
guard. The counter protects against inode/per-directory/syscall bombs made of many
tiny entries and is independent of byte and ratio limits.

Only members that will create disk entries SHALL count: selector exclusions, user
filter skips, and members dropped before writing do not increment the counter.
Every written FILE, DIR, SYMLINK, and HARDLINK counts. This is a global resource
guard and halts even under `OnError.CONTINUE`.

#### Scenario: entry-count matrix

| Case | Expected |
| --- | --- |
| More than `max_entries` members are written | `ExtractionError` once the count crosses the limit; extraction halts under any `OnError` |
| `ExtractionLimits(max_entries=100)` | Error after the 100th written member when the 101st would be written |
| Selector chooses one member from millions | Extraction can complete with `max_entries=1` because only selected written entries count |
| Many tiny files stay below byte/ratio limits but exceed entry count | Entry-count guard still raises |

### Requirement: Symlink extraction is target-independent and fails safe on unsupported filesystems

The system SHALL create SYMLINK members as symbolic references via
`os.symlink()` without requiring the target to exist, to be selected, or to be
inside the archive. A symlink may dangle and no target data is copied; the
universal resolved-target escape check remains the only safety constraint on the
link target.

If the platform or destination filesystem cannot create symlinks and
`os.symlink()` raises `OSError` or `NotImplementedError`, the member SHALL be a
per-member failure governed by `OnError`. Archivey does not silently copy target
data as Python `tarfile` may do on symlink-unsupported platforms.

#### Scenario: symlink extraction matrix

| Case | Expected |
| --- | --- |
| SYMLINK target member is excluded by selector/filter and resolved target stays in `dest` | Symlink is created and may dangle; target data is not copied |
| SYMLINK target appears later or outside the archive but stays in `dest` | Symlink is created as stored; no target materialization |
| `os.symlink` unsupported raises `OSError`/`NotImplementedError` | `STOP` raises; `CONTINUE` records `FAILED`; no copy fallback |

### Requirement: Live archive-wide decompression ratio for unknown-size streams

The system SHALL evaluate a live archive-wide ratio during extraction when no
per-member `compressed_size` and no cheap static `compressed_source_size` is
available, but the compressed backend can expose `compressed_bytes_consumed`.
This covers compressed archives from non-seekable pipes and seekable opaque
streams whose size is not cheaply knowable. Backends wrap the stream source in
the counting reader exactly when the static denominator is absent.

The ratio SHALL be `cumulative_bytes_written / compressed_bytes_consumed`, checked
after cumulative output crosses `ratio_activation_threshold` using the same
`max_ratio`. It is a cumulative global guard: if it trips, extraction halts even
under `OnError.CONTINUE`. The live path complements static checks and is not used
when member compressed sizes or a cheap outer source size provide a denominator;
whichever available guard trips first wins. Codec-layer seeks may re-read counted
bytes, inflating the denominator and weakening the guard, but never causing a
false positive.

#### Scenario: live archive-wide ratio matrix

| Case | Expected |
| --- | --- |
| Highly compressible `.tar.gz` from non-seekable pipe has no static denominator | Live ratio raises `ExtractionError` after threshold before absolute byte cap |
| Live ratio exceeded under `OnError.CONTINUE` | `ExtractionError` propagates and extraction halts |
| Plain uncompressed `.tar` from a pipe | Consumed and written bytes stay about 1:1; live ratio does not trip; byte limit still applies |
| `.tar.gz` has cheap `compressed_source_size` | Static archive-wide ratio is used; live path is not engaged/double-counted |
| Seekable opaque compressed stream has no cheap size/`size`/`try_get_size()`/O(1) end seek | Source is counted live; archive is not left with only the byte cap |
