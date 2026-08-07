# access-mode-and-cost — ordered `StreamCapability` delta

## MODIFIED Requirements

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

@functools.total_ordering
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

`StreamCapability` SHALL be **totally ordered by strength**, weakest first:
`FORWARD_ONLY < SEEKABLE`. A seekable source can serve every read a forward-only one
can, so the ordering is the "is at least as strong as" relation and comparing two
capabilities SHALL answer whether one source shape satisfies a requirement stated as
the other. `ListingCost` and `AccessCost` are **not** ordered: their members name
kinds of work, not strengths of the same resource.

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

#### Scenario: stream capability ordering matrix

| Case | Expected |
| --- | --- |
| `FORWARD_ONLY < SEEKABLE` | `True` |
| `SEEKABLE >= FORWARD_ONLY` | `True` |
| `FORWARD_ONLY <= FORWARD_ONLY` | `True` |
| `SEEKABLE < FORWARD_ONLY` | `False` |
| Comparison against a non-`StreamCapability` | `TypeError` |
| `sorted(StreamCapability)` | `[FORWARD_ONLY, SEEKABLE]` |
