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

#### Scenario: open mode matrix

| Case | Expected |
| --- | --- |
| `streaming=False` on indexed ZIP | Central directory loaded; random access available |
| `streaming=True` on `.tar.gz` | No full-archive index scan; members as stream is read |
| `streaming=False` on non-seekable source that needs seek | Error at open (before member data); caller must use `streaming=True` (or supply a seekable source) — library does not buffer |

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
consumes it. `members_report_if_available()` never begins/advances/consumes the pass.

#### Scenario: streaming enforcement matrix

| Case | Expected |
| --- | --- |
| `get` / `members` / `open` / `read` on `streaming=True` | `UnsupportedOperationError` |
| First `__iter__` or `stream_members` | Yields in archive order |
| Second forward-pass method after begin/complete | `UnsupportedOperationError` (all formats) |
| Early `break` then `scan_members()` | Drains remainder; fully-resolved list; later pass methods raise |
| `scan_members()` then `stream_members()` on fresh streaming reader | List returned; subsequent pass raises (any index topology) |

### Requirement: members_report_if_available() — an index-only member report

`members_report_if_available() -> MemberListReport | None` is **index-only**: no
forward scan, no member-data reads, never consumes the pass. Returns the report from
an upfront index or already-materialized listing; else `None`. Guaranteed
fully-resolved list → `members()` (RA) or `scan_members()` (either mode).

| Index topology | Availability |
| --- | --- |
| Leading (directory, ISO) | Both modes |
| Trailing (ZIP CD, 7z EOF header) | Both modes today (those backends require seekable sources; `SUPPORTS_STREAMING_NON_SEEKABLE` is false). Future trailing+non-seekable → `None` on non-seekable |
| No-index (TAR) | `None` until a completed forward pass / `scan_members` / `members` |

Index-only listings SHALL leave data-stored link targets unset (`link_target` /
`link_target_member`); resolving them needs member-data reads that
`members()`/`scan_members()` perform.

#### Scenario: index-only listing matrix

| Case | Expected |
| --- | --- |
| Streaming ZIP (upfront index) | Full list; no scan/data read; forward pass still available |
| No-index, not yet iterated | `None` |
| No-index after completed pass / `scan_members` | Fully-resolved materialized list |
| ZIP symlink via `members_report_if_available` | Link fields unset; `members`/`scan_members` resolve them |

### Requirement: Access mode × method behaviour summary

The system SHALL behave per this canonical table (`✅` allowed,
`⛔` → `UnsupportedOperationError`):

| Method | `streaming=False` | `streaming=True` |
| --- | --- | --- |
| `__iter__` | ✅ repeatable (cache after first) | ✅ **once** (no replay) |
| `stream_members` | ✅ | ✅ once |
| `extract_all` | ✅ | ✅ once |
| `scan_members` | ✅ (= `members`) | ✅ finishes/returns pass |
| `members_report_if_available` | ✅ index-only (may be `None`) | ✅ index-only, no-consume |
| `members` / `get` / `open` / `read` | ✅ | ⛔ |
| `in` (identity) | ✅ no scan | ✅ no scan |
| `cost` / `info` / `format` / `close` / CM | ✅ | ✅ |
| at `open_archive()` | fail fast if source not RA-capable | any source |

In streaming mode, `__iter__` / `stream_members` / `extract_all` share one pass.
Backend `_SUPPORTS_RANDOM_ACCESS` may also force `open`/`read` to raise; it
composes with — does not replace — these rules.

#### Scenario: summary checks

| Case | Expected |
| --- | --- |
| `scan_members()` either mode | Fully-resolved list (RA ≡ `members()`; streaming finishes pass) |
| Full streaming `__iter__`, then iterate again | Second → `UnsupportedOperationError` |

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

`streaming` SHALL remain the only access-mode choice. `member_streams` SHALL
declare stream capabilities **within** a mode (not a third mode; no
`ArchiveyConfig` equivalent). Ownership, leases, materialization, and free-threaded
rules for `MemberStreams.CONCURRENT` live in `reader-concurrency`; this
requirement only states how those flags compose with `streaming`.

| Mode | `member_streams` composition |
| --- | --- |
| `streaming=False` | `CONCURRENT` and/or `SEEKABLE` MAY be declared; concurrent-open semantics are `reader-concurrency`. Without `CONCURRENT`, one live member stream (`archive-reading`). |
| `streaming=True` | Random `open`/`read` still unavailable. Single progressive pass is exclusive. **`CONCURRENT` incompatible** → `ArchiveyUsageError` at open. `SEEKABLE` alone MAY be declared. |

Random-access `stream_members()` remains exclusive even when random `open()` is
otherwise available (simultaneous streams use materialize + random `open()` under
`CONCURRENT` — see `reader-concurrency`). Detected pass/open/close overlap → later
op `ArchiveyUsageError`; active pass stays usable. Ops after `reader.close()` →
`ArchiveyUsageError` (idempotent `close`).

#### Scenario: mode × capability matrix

| Case | Expected |
| --- | --- |
| `streaming=True` + `CONCURRENT` | `ArchiveyUsageError` at open; no reader |
| RA + `CONCURRENT` (or without) | Concurrent-open / single-live-stream rules per `reader-concurrency` / `archive-reading` |
| Active pass + conflicting pass/open/close | Later → `ArchiveyUsageError`; original pass usable |
| RA `stream_members` active + `open()` | `ArchiveyUsageError` |
| `extract_all` drives child `stream_members` | Permitted composition; unrelated public pass rejected |

### Requirement: Concurrent-stream cost is informational

`access_cost` / `solid_block_count` describe work (including under a declared
simultaneous schedule). They SHALL NOT permit or deny capabilities —
`member_streams` is the only gate (`reader-concurrency`). Solid open-*order* cost
is reported here and steered toward `stream_members()`, not gated.

#### Scenario: cost vs capability

| Case | Expected |
| --- | --- |
| `CONCURRENT` on `DIRECT` and `SOLID` readers, multiple streams | Both supported and byte-correct; only reported/repeated work differs |
