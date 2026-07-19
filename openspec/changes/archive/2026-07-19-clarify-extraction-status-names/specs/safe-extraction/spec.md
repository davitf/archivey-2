## MODIFIED Requirements

### Requirement: Per-ArchiveMember ExtractionResult with Status

`ExtractionReport.results` SHALL contain one `ExtractionResult` for every
selected member the coordinator processes when the operation completes, including
members blocked by universal/policy checks before the user filter. Selector
exclusions are outside the operation and have no result; a user `filter` that
returns `None` likewise drops the member with **no** `ExtractionResult` (it is a
caller-elected exclusion, not an extraction outcome).

```python
@dataclass(frozen=True)
class ExtractionResult:
    member: ArchiveMember
    path: Path | None
    status: ExtractionStatus
    error: ArchiveyError | OSError | None = None
    requested_path: Path | None = None

class ExtractionStatus(str, Enum):
    EXTRACTED = "extracted"
    NOT_OVERWRITTEN = "not_overwritten"
    SUPERSEDED = "superseded"
    BLOCKED = "blocked"
    FAILED = "failed"
```

Statuses SHALL mean: `EXTRACTED` created an entry (`path` set, `error=None`);
`NOT_OVERWRITTEN` left an existing destination in place because
`OverwritePolicy.SKIP` found one (`path=None`, `error=None`); `SUPERSEDED` is a
non-current duplicate skipped by the hardwired last-entry-wins rule (`path=None`,
`error=None`); `BLOCKED` is a continued `FilterRejectionError` (a universal
path-safety check or a policy filter blocked the member); `FAILED` is a continued
non-rejection per-member `ArchiveyError` or permitted filesystem `OSError`.
`NOT_OVERWRITTEN` and `SUPERSEDED` are not failures and emit no diagnostic.
`requested_path` carries the destination the coordinator intended before
overwrite/rename resolution; it equals `path` for an ordinary write, and
`requested_path != path and status == EXTRACTED` marks an `OverwritePolicy.RENAME`
(see the cross-platform name-safety requirement).

Continued `BLOCKED`/`FAILED` results SHALL emit exactly one matching
`EXTRACTION_MEMBER_BLOCKED` / `EXTRACTION_MEMBER_FAILED` occurrence per result.
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
| User filter returns `None` | No `ExtractionResult`; no result-count impact (like a selector exclusion) |
| Selector excludes member | No `ExtractionResult`; no result-count impact |
| Member blocked by `PathTraversalError` under `CONTINUE` | Result is `BLOCKED` with matching error and diagnostic |
| Member write raises `OSError` under `CONTINUE` | Result is `FAILED` with matching error and diagnostic |
| Member written successfully | Result is `EXTRACTED`, `path` points to created entry |
| Existing destination under `OverwritePolicy.SKIP` | Result is `NOT_OVERWRITTEN`, `path=None` |
| One failed source causes three hardlink results to fail | Failed count increases by three; retained contexts, if budget permits, share one failure group id/size |

### Requirement: Skip non-current members by default

`extract` / `extract_all` SHALL skip members with `is_current is False` by default
(`ExtractionStatus.SUPERSEDED`; no write; no bomb-limit counting for the skip). This
is **hardwired coordinator behavior**, not the policy `filter` / `MemberFilter`
pipeline: the skip happens after the optional user `filter` runs so callers can
inspect or rewrite non-current members, then the coordinator still skips writing
them unless a future explicit opt-in lands. `SUPERSEDED` is distinct from
`ExtractionStatus.NOT_OVERWRITTEN` (an existing destination left in place under
`OverwritePolicy.SKIP`).

How surfaces interact:

| Surface | Non-current members |
| --- | --- |
| `members()` / `__iter__` / `get` | Visible (metadata + `is_current=False`) |
| `members=` selector | May select them; they still participate in the extract walk |
| User `filter` (`MemberFilter`) | **Invoked** on them (same as current members) |
| Default extract write | Skipped after filter; `SUPERSEDED` result |
| `open`/`read` on superseded `FILE` | Still allowed (payload exists); not gated by `is_current` |

There is no extract-all flag in this change to force writing non-current
revisions; callers that need those bytes use `open`/`read` (or a future opt-in).

#### Scenario: non-current skip matrix

| Case | Expected |
| --- | --- |
| Content superseded by later same-name or anti | `SUPERSEDED` on extract; path absent on fresh dest |
| User `filter` receives non-current member | Filter is called; returning the member does not force a write |
| `open` superseded content `FILE` | Bytes returned (random access still works) |

### Requirement: Overwrite Policy

The system SHALL enforce `OverwritePolicy` whenever a destination entry already
exists at the transformed member path:

```python
class OverwritePolicy(Enum):
    ERROR = "error"
    SKIP = "skip"
    REPLACE = "replace"
    RENAME = "rename"
```

`ERROR` raises a per-member `ExtractionError` governed by `OnError`; `SKIP`
records a `NOT_OVERWRITTEN` result and is not a failure. Existence checks SHALL use
`lstat` semantics so dangling symlinks count as existing entries. `REPLACE` SHALL
be atomic and never write through a symlink: FILE data streams to a temp file in
the destination directory, metadata is applied, and `os.replace()` moves it onto
the destination. A mid-stream failure preserves the old entry and discards only
the temp. DIR/SYMLINK/HARDLINK replacement removes the existing entry and creates
fresh; replacing an existing directory with a file removes the directory first.
`RENAME` writes a colliding entry under a deterministic derived name (`name (1)`,
inserted before the final suffix) rather than overwriting — see the cross-platform
name-safety requirement.

#### Scenario: overwrite matrix

| Case | Expected |
| --- | --- |
| Existing path under `ERROR` | `ExtractionError`; existing entry unmodified |
| Existing path under `SKIP` | `ExtractionResult.status == NOT_OVERWRITTEN`, `path=None`, no exception |
| Existing file under `REPLACE` | Fresh file is written via temp file + `os.replace()` |
| Existing symlink under `REPLACE` | Symlink entry itself is replaced; bytes never follow the old link |
| `REPLACE` fails mid-stream | Existing file remains unchanged; temp is discarded |
| Dangling symlink under `ERROR` or `SKIP` | Treated as existing; no write-through to target |

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
`NOT_OVERWRITTEN` links under `SKIP`, link further selected links to the
materialized path, and write nothing if every selected link is skipped. The
materialized file gets the selected link's transformed metadata. An equivalent
hidden temp inside `dest` is permitted.

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
| First selected link destination exists under `OverwritePolicy.SKIP` | That link result is `NOT_OVERWRITTEN`; content moves to the next allowed link; all skipped means no write |
| Excluded source on a forward-only stream | Per-member failure: `STOP` raises; `CONTINUE` records `FAILED` and proceeds |
| HARDLINK appears before its also-selected source | After the pass it links to the extracted source inode; source bytes read and counted once |

### Requirement: Error Policy (OnError) for extraction failures

`OnError.STOP` and `OnError.CONTINUE` SHALL govern per-member failures only.
Under `CONTINUE`, a member-scoped `FilterRejectionError`, other member-scoped
`ArchiveyError`, permitted read/write `OSError`, or per-member ratio violation
records `BLOCKED`/`FAILED`, removes partial output, emits the matching diagnostic
under the active diagnostic policy, and proceeds.

Diagnostic disposition SHALL still be authoritative: `RAISE` emits
`DiagnosticRaisedError` and halts immediately even under `OnError.CONTINUE`;
logging-handler and diagnostic-callback exceptions propagate unchanged. Under
`STOP`, the genuine rejection/failure raises immediately and is not converted to
an extraction advisory. Global resource guards (`ResourceLimitError` for
cumulative bytes, archive-wide/live ratio, and max entries), `KeyboardInterrupt`,
`MemoryError`, and unexpected programming exceptions are always-stop and are not
swallowed.

#### Scenario: OnError matrix

| Case | Expected |
| --- | --- |
| Corrupt member under `CONTINUE` and default diagnostics | Partial output removed; `FAILED` result; `EXTRACTION_MEMBER_FAILED`; later members continue |
| Extraction diagnostic resolves to `RAISE` under `CONTINUE` | `DiagnosticRaisedError` halts; no report returned |
| `CorruptionError` under `STOP` | Original `CorruptionError` propagates; no continued-failure diagnostic |
| Filesystem `OSError` while writing under `CONTINUE` | Partial output removed; `FAILED` result/diagnostic; extraction proceeds |
