# Access Mode and Cost

## Purpose

Callers declare access pattern via `streaming: bool` at open, and read a
machine-readable `CostReceipt` (`listing` / `access` / `stream` axes + solid
block count). This spec is the **canonical** access-mode × method table;
`archive-reading` summarizes the same rules for the reader surface.

## Related specs

| Spec | Relationship |
| --- | --- |
| `archive-reading` | Reader API that obeys these rules |
| `reader-concurrency` | `MemberStreams.CONCURRENT` within random-access mode |
| `diagnostics` | Runtime rewind / seek-index events (not frozen into `CostReceipt`) |
| `safe-extraction` | `extract_all` as a forward-pass entry point |
## Requirements
### Requirement: Declaring access mode at open_archive()

`open_archive(..., streaming: bool = False)` SHALL accept exactly two modes:

| Mode | Meaning |
| --- | --- |
| `streaming=False` (default) | **Random access.** Load indexes when available. Fail fast at open if the source is non-seekable and the format cannot adapt — never silently degrade to forward-only. Seek points for single-stream formats are built **lazily** on first `seek()`. |
| `streaming=True` | **Forward-only, single pass.** Disable index loading where possible; works on non-seekable sources. Random-access / full-materialization APIs disabled **uniformly** (independent of any loaded index). `members_report_if_available()` stays callable (never scans). |

Non-seekable sources are never given random access: with `streaming=False` the
library fails fast at open when the format needs seek (it does not buffer the
source into memory or a temp file). Use `streaming=True` for pipes/sockets.
Eager seek-point building is not exposed.

A **seekable** stream source is wrapped in a fixed-size read buffer at the source
boundary so `read(n)` returns the full count (`ensure_full_count_reads`) — a raw
`read(n)` may legally return short, and header parsers, archivey's and the stdlib's
alike, read a short return as EOF. That is bounded readahead over a source the caller
already made seekable, not the materialization forbidden above: it never converts a
non-seekable source, and never copies the archive into memory or a temp file. A path
source has always paid the same cost through `open()`'s `BufferedReader`.

#### Scenario: open mode matrix

| Case | Expected |
| --- | --- |
| `streaming=False` on indexed ZIP | Central directory loaded; random access available |
| `streaming=True` on `.tar.gz` | No full-archive index scan; members as stream is read |
| `streaming=False` on non-seekable source that needs seek | Error at open (before member data); caller must use `streaming=True` (or supply a seekable source) — library does not buffer |
| Seekable stream source, either mode | Buffered at the source boundary for full-count `read(n)`; bounded readahead only — never materialized to memory or disk |

### Requirement: Access-mode enforcement — streaming is forward-only

On `streaming=True`, `members()` / `get()` / `open()` / `read()` SHALL raise
`UnsupportedOperationError` uniformly. No `__len__`/`__getitem__`
(`archive-reading`); `member in reader` is scan-free identity membership (both
modes).

Forward-pass entry points: `__iter__`, `stream_members`, `extract_all`. The first
consumes the single pass; any later call to any of them SHALL raise — even after
completion (no streaming `__iter__` cache-replay). Early `break` still consumes.
Member selection for extraction is `extract_all(members=...)` (`safe-extraction`).

`scan_members()` MAY run before the pass (starts+finishes it), after an interrupted
pass (drains remainder), or after completion (returns cache). Starting the pass
consumes it. `members_report()` MAY likewise start or finish the pass and consumes
it; it returns `MemberListReport` instead of raising on terminal archive-level
listing errors (`archive-reading`). `members_report_if_available()` never
begins/advances/consumes the pass.

On both access modes, `__iter__` and `stream_members` SHALL yield every
recovered member before propagating a terminal archive-level listing error
(yield-then-raise). `members()` / `scan_members()` remain complete-or-raise.

#### Scenario: streaming enforcement matrix

| Case | Expected |
| --- | --- |
| `get` / `members` / `open` / `read` on `streaming=True` | `UnsupportedOperationError` |
| First `__iter__` or `stream_members` | Yields in archive order |
| Terminal archive error after prefix (either mode) | Prefix yielded; then raise |
| Second forward-pass method after begin/complete | `UnsupportedOperationError` (all formats) |
| Early `break` then `scan_members()` | Drains remainder; fully-resolved list or raise; later pass methods raise |
| `scan_members()` then `stream_members()` on fresh streaming reader | List returned when complete; subsequent pass raises (any index topology) |
| `members_report()` on streaming with terminal archive error after prefix | Report with prefix + `error`; pass consumed; no raise from `members_report` |

### Requirement: members_report_if_available() — a report peek

`members_report_if_available() -> MemberListReport | None` is a **report peek**:
no forward scan, no member-data reads, never consumes the pass. It returns the
stored `MemberListReport` (complete or incomplete) when one exists without scanning,
or the upfront index as a complete report for backends that carry one; else `None`.
Guaranteed fully-resolved complete list → `members()` (RA) or `scan_members()`
(either mode).

| Index topology | Availability |
| --- | --- |
| Leading (ISO) | Both modes, as complete report |
| Scan-based (directory) | `None` until a pass completes — a filesystem walk is not an index (its `listing_cost` is `REQUIRES_SCANNING`), so it has nothing to peek at |
| Trailing (ZIP CD, 7z EOF header) | Both modes today, as complete report (those backends require seekable sources; `SUPPORTS_STREAMING_NON_SEEKABLE` is false). Future trailing+non-seekable → `None` on non-seekable |
| No-index (TAR), no prior materialization/pass | `None` |
| No-index after completed successful pass / `scan_members` / `members` | Complete report |
| No-index after a terminal archive error was stored after a recoverable prefix | Incomplete report (`members` is prefix, `error` set); count is a floor |

Index-only listings SHALL leave data-stored link targets unset (`link_target` /
`link_target_member`); resolving them needs member-data reads that
`members()`/`scan_members()` perform. Returning an incomplete report to a caller
MUST NOT change the complete-or-raise behaviour of `members()` / `scan_members()` /
`get(name)`; the report self-labels via `error` and those methods still raise.

#### Scenario: index-only listing matrix

| Case | Expected |
| --- | --- |
| Streaming ZIP (upfront index) | Full list; no scan/data read; forward pass still available |
| No-index, not yet iterated | `None` |
| Directory archive, either mode, not yet iterated | `None` — consistent with its own `listing_cost=REQUIRES_SCANNING` |
| No-index after completed pass / `scan_members` | Complete fully-resolved report |
| No-index after incomplete pass already ran | Incomplete report with recovered prefix and `error` |
| ZIP symlink via `members_report_if_available` | Link fields unset; `members`/`scan_members` resolve them |

### Requirement: Access mode × method behaviour summary

The system SHALL behave per this canonical table (`✅` allowed,
`⛔` → `UnsupportedOperationError`):

| Method | `streaming=False` | `streaming=True` |
| --- | --- | --- |
| `__iter__` | ✅ repeatable after **successful** complete cache; yield-then-raise on terminal archive error | ✅ **once** (no replay); yield-then-raise on terminal archive error |
| `stream_members` | ✅; yield-then-raise on terminal archive error | ✅ once; yield-then-raise |
| `extract_all` | ✅; RA extract-prep fail-closed on terminal listing error | ✅ once; streaming write-then-raise |
| `scan_members` | ✅ (= `members`); complete-or-raise | ✅ finishes/returns pass; complete-or-raise |
| `members_report` | ✅ always returns `MemberListReport` | ✅ may consume pass; always returns report |
| `members_report_if_available` | ✅ report peek: stored report (complete or incomplete) / upfront index / `None`; never scans | ✅ report peek, no-consume |
| `members` / `get` / `open` / `read` | ✅; `members`/`get` complete-or-raise | ⛔ |
| `in` (identity) | ✅ no scan (incl. recovered report members) | ✅ no scan |
| `cost` / `info` / `format` / `close` / CM | ✅ | ✅ |
| at `open_archive()` | fail fast if source not RA-capable | any source |

In streaming mode, `__iter__` / `stream_members` / `extract_all` share one pass.
Backend `_SUPPORTS_RANDOM_ACCESS` may also force `open`/`read` to raise; it
composes with — does not replace — these rules.

#### Scenario: summary checks

| Case | Expected |
| --- | --- |
| `scan_members()` either mode on clean archive | Fully-resolved list (RA ≡ `members()`; streaming finishes pass) |
| Full streaming `__iter__`, then iterate again | Second → `UnsupportedOperationError` |
| RA `__iter__` on TAR rejected-header after prefix | Yields prefix members, then `CorruptionError` |
| `members_report()` row present either mode | ✅ returns report |

### Requirement: Exposing a CostReceipt describing access costs

Every opened archive SHALL expose `ar.cost` (also in `ar.info.cost`), computed
during open before heavy I/O. Three **orthogonal** axes + solid-block count:

```python
class ListingCost(Enum):
    INDEXED = "indexed"                     # O(1) listing via index/CD
    REQUIRES_SCANNING = "requires_scanning" # header-to-header, no payload decode
    REQUIRES_DECOMPRESSION = "requires_decompression"  # must decompress to list

class AccessCost(Enum):
    DIRECT = "direct"  # member N independent of others
    SOLID = "solid"    # may need earlier bytes in the block

class StreamCapability(Enum):
    SEEKABLE = "seekable"        # source seekable
    FORWARD_ONLY = "forward_only"  # pipe/socket; revisit needs a new stream

@dataclass(frozen=True)
class CostReceipt:
    listing_cost: ListingCost
    access_cost: AccessCost
    stream_capability: StreamCapability
    solid_block_count: int | None  # distinct solid blocks, or None
    notes: tuple[str, ...] = ()    # caveats — not an occurrence log
```

| Axis | About |
| --- | --- |
| `stream_capability` | Source bytes — can they be `seek()`ed? |
| `access_cost` | Format layout — `DIRECT` vs `SOLID` (re-decompress cost lives here, not in seekability) |
| `listing_cost` | Enumerating names+metadata |

Examples: ZIP file → `INDEXED`+`DIRECT`+`SEEKABLE`; plain tar file →
`REQUIRES_SCANNING`+`DIRECT`+`SEEKABLE`; tar on pipe → same + `FORWARD_ONLY`;
`.tar.gz` file → `REQUIRES_DECOMPRESSION`+`SOLID`+`SEEKABLE`; solid 7z →
`INDEXED`+`SOLID`+`SEEKABLE` with `solid_block_count` = folder count.

#### Scenario: cost receipt matrix

| Case | Expected |
| --- | --- |
| Successful open | `ar.cost` populated without separate member scan/read |
| ZIP | `listing_cost=INDEXED`, `access_cost=DIRECT` |
| `.tar.gz` | `REQUIRES_DECOMPRESSION` + `SOLID` |
| Same plain tar: file vs pipe | `stream_capability` SEEKABLE vs FORWARD_ONLY; `access_cost=DIRECT` both |
| Solid 7z, multiple folders | `info.is_solid`, `access_cost=SOLID`, `solid_block_count` = folder count |

### Requirement: CostReceipt remains an immutable open-time cost description

`CostReceipt` SHALL describe static open-time properties only — not runtime
diagnostics. Slow rewinds / seek-index failures go to reader/stream diagnostic
aggregates (`diagnostics`). Static `notes` MAY caveat capability; SHALL NOT act as
an occurrence log or counter.

#### Scenario: cost immutability matrix

| Case | Expected |
| --- | --- |
| Backward seek re-decompresses | `STREAM_REWIND_REDECOMPRESSES` on diagnostics; `CostReceipt` unchanged |
| Optional seek-index degrades | `SEEK_INDEX_DEGRADED` on aggregate; no diagnostic field on `CostReceipt` |

### Requirement: Declared capabilities compose with the two access modes

`streaming` SHALL remain the only access-mode choice. `seekable_members` /
`concurrent_members` SHALL declare stream capabilities **within** a mode (not a third
mode; no `ArchiveyConfig` equivalent). Ownership, leases, materialization, and
free-threaded rules for declared concurrency live in `reader-concurrency`; this
requirement only states how the capabilities compose with `streaming`.

| Mode | Capability composition |
| --- | --- |
| `streaming=False` | `concurrent_members` and/or `seekable_members` MAY be declared; concurrent-open semantics are `reader-concurrency`. Without `concurrent_members`, one live member stream (`archive-reading`). |
| `streaming=True` | Random `open`/`read` still unavailable. Single progressive pass is exclusive. **`concurrent_members=True` incompatible** → `ArchiveyUsageError` at open. `seekable_members=True` alone MAY be declared. |

Random-access `stream_members()` remains exclusive even when random `open()` is
otherwise available (simultaneous streams use materialize + random `open()` under
`concurrent_members=True` — see `reader-concurrency`). Detected pass/open/close overlap →
later op `ArchiveyUsageError`; active pass stays usable. Ops after `reader.close()` →
`ArchiveyUsageError` (idempotent `close`).

Defaults and behaviour are unchanged by the spelling: this requirement previously
described the same composition in terms of a `member_streams` flag enum.

#### Scenario: mode × capability matrix

| Case | Expected |
| --- | --- |
| `streaming=True` + `concurrent_members=True` | `ArchiveyUsageError` at open; no reader |
| RA + `concurrent_members=True` (or without) | Concurrent-open / single-live-stream rules per `reader-concurrency` / `archive-reading` |
| Active pass + conflicting pass/open/close | Later → `ArchiveyUsageError`; original pass usable |
| RA `stream_members` active + `open()` | `ArchiveyUsageError` |
| `extract_all` drives child `stream_members` | Permitted composition; unrelated public pass rejected |

### Requirement: Concurrent-stream cost is informational

`access_cost` / `solid_block_count` describe work (including under a declared
simultaneous schedule). They SHALL NOT permit or deny capabilities —
`concurrent_members` is the only gate (`reader-concurrency`). Solid open-*order* cost
is reported here and steered toward `stream_members()`, not gated.

**An out-of-order `open()` on a solid archive SHALL emit no diagnostic and no warning,
and this is deliberate.** Three separate reviews have now proposed adding one, so the
reason is recorded here rather than rediscovered a fourth time:

- The signal already exists, *earlier*, and is impossible to miss: `cost.access_cost`
  is `SOLID` on the receipt every reader publishes at open, before the caller does
  anything. Compare the rewind case (`seekable-decompressor-streams`), which has **no**
  open-time signal at all and is the reason that one does emit — if the case with no
  prior warning does not justify an ambient one, the case that already told you at open
  certainly does not.
- A `warnings.warn` here would be the library's first, against the project rule that
  prefers structured diagnostics precisely because "a logging warning most applications
  never see is a surprise deferred, not avoided" (`VISION.md`).
- Emitting per `open()` on the workload that provokes it (walking every member of a
  solid archive) would produce one occurrence per member — noise proportional to the
  thing already reported once, accurately, as a cost.

The library's answer to solid open-order cost is the receipt plus the `open()`
docstring's pointer to `stream_members()`. A caller who wants to be *stopped* has
`ListingLimits` and the extraction ratio guards, not an advisory.

#### Scenario: cost vs capability

| Case | Expected |
| --- | --- |
| `concurrent_members=True` on `DIRECT` and `SOLID` readers, multiple streams | Both supported and byte-correct; only reported/repeated work differs |
| Out-of-order `open()` on a solid 7z / RAR / compressed TAR | Members read correctly; **no** diagnostic and **no** `warnings.warn`; `cost.access_cost == SOLID` was the signal, at open |

