# Access Intent and Cost

## Purpose

Allows callers to declare upfront how they intend to access an archive (sequential vs. random), and exposes a machine-readable `CostReceipt` describing the actual listing cost, per-member access cost, stream seekability, and solid-block count. Together these two mechanisms let callers make informed access decisions and let the library enforce contracts that prevent accidentally expensive operations.

## Requirements

### Requirement: Declaring access intent at open()

The system SHALL accept an `intent` parameter in `archivey.open()` that the caller uses to declare their intended access pattern. The library uses this declaration to optimize backend initialization and to enforce access constraints.

```python
class Intent(Enum):
    AUTO       = "auto"       # library chooses optimal access mode
    SEQUENTIAL = "sequential" # caller promises forward-only iteration; disables index loading
    RANDOM     = "random"     # caller needs random access; library fails fast if impossible
```

- `Intent.AUTO`: the library selects the most appropriate mode for the detected format. Index structures (central directories, 7z headers) are loaded when available.
- `Intent.SEQUENTIAL`: the caller promises forward-only, single-pass iteration. The library MUST disable index loading where possible, avoiding the upfront cost of scanning or parsing a central directory. Random-access operations (`__getitem__`, `get`, random `extract`) are disabled on sequential readers unless the backend can satisfy them cheaply via an already-loaded in-memory index.
- `Intent.RANDOM`: the caller requires random member access. The library SHALL fail fast at `open()` time if the format or source cannot support random access (e.g. a non-seekable stream for a format that requires seek).

#### Scenario: AUTO intent on an indexed format

- **WHEN** `archivey.open("archive.zip", intent=Intent.AUTO)` is called
- **THEN** the ZIP central directory is read upfront and random access is available

#### Scenario: SEQUENTIAL intent disables index loading

- **WHEN** `archivey.open("archive.tar.gz", intent=Intent.SEQUENTIAL)` is called
- **THEN** the library does not attempt to scan the full archive to build an index, and members are yielded as the stream is read

#### Scenario: RANDOM intent fails fast on non-seekable source

- **WHEN** `archivey.open(non_seekable_stream, intent=Intent.RANDOM)` is called on a format that requires seek
- **THEN** an appropriate error is raised at open time, before any member data is read

---

### Requirement: Intent enforcement — SEQUENTIAL disables random access

The system SHALL raise `UnsupportedOperationError` when a random-access operation is attempted on a reader opened with `Intent.SEQUENTIAL`, unless the backend already has an in-memory index loaded and can satisfy the lookup cheaply.

Random-access operations that are subject to this constraint:
- `__getitem__(name)` (`ar["file.txt"]`)
- `get(name, default)`
- random single-member `extract(member, dest)`

Additionally, `members()` and `__len__` (which require materializing all members) SHALL raise `UnsupportedOperationError` on a `SEQUENTIAL`-intent reader.

#### Scenario: key lookup raises UnsupportedOperationError under SEQUENTIAL

- **WHEN** `ar["file.txt"]` is called on a reader opened with `Intent.SEQUENTIAL`
- **AND** the backend has no pre-loaded in-memory index
- **THEN** `UnsupportedOperationError` is raised

#### Scenario: members() raises UnsupportedOperationError under SEQUENTIAL

- **WHEN** `ar.members()` or `len(ar)` is called on a reader opened with `Intent.SEQUENTIAL`
- **THEN** `UnsupportedOperationError` is raised

---

### Requirement: Exposing a CostReceipt describing access costs

The system SHALL compute and expose a `CostReceipt` for every opened archive, available via `ar.cost` and embedded in `ar.info.cost`. The receipt SHALL be computed during `open_read()` before any heavy I/O and SHALL describe:

- **listing cost**: whether enumerating all members is O(1) (index present) or O(N) (full stream scan required);
- **access cost**: whether reading a member requires decompressing only that member (DIRECT) or also all preceding members in the same solid block (SOLID);
- **stream capability**: whether the source supports arbitrary seeking or is replay-only;
- **solid-block count**: for solid archives, the number of distinct solid blocks that must each be decompressed separately.

```python
class ListingCost(Enum):
    O1  = "o1"   # central directory / index present; O(1) regardless of archive size
    ON  = "on"   # no index; must scan entire stream to enumerate members

class AccessCost(Enum):
    DIRECT = "direct"   # random access to any member without reading others
    SOLID  = "solid"    # decompressing member N requires decompressing members 0..N-1

class StreamCapability(Enum):
    SEEKABLE     = "seekable"       # source supports arbitrary seeking
    REPLAY_ONLY  = "replay_only"    # non-seekable; rewinding is impossible

@dataclass(frozen=True)
class CostReceipt:
    listing_cost: ListingCost
    access_cost: AccessCost
    stream_capability: StreamCapability
    is_solid: bool
    solid_block_count: int | None   # 7z: number of solid blocks (each requires one pass)
    notes: tuple[str, ...] = ()     # human-readable caveats
```

Each backend computes its `CostReceipt` in `open_read()`. Examples:

- **ZIP**: `ListingCost.O1` (EOCD parsed), `AccessCost.DIRECT` (per-member offsets in central directory), `StreamCapability.SEEKABLE`.
- **TAR.GZ**: `ListingCost.ON` (no central directory), `AccessCost.SOLID` (single gzip stream), `StreamCapability.SEEKABLE` or `REPLAY_ONLY` depending on source.
- **7z**: `ListingCost.O1` (header block at start), `AccessCost.SOLID` if multiple members share a solid folder, `solid_block_count` from `archiveinfo().blocks`.

#### Scenario: CostReceipt available immediately after open

- **WHEN** an archive is opened successfully
- **THEN** `ar.cost` is populated without requiring a separate scan or read of member data

#### Scenario: ZIP reports O1 listing cost

- **WHEN** a ZIP archive is opened
- **THEN** `ar.cost.listing_cost == ListingCost.O1`
- **AND** `ar.cost.access_cost == AccessCost.DIRECT`

#### Scenario: TAR.GZ reports ON listing cost and SOLID access

- **WHEN** a `.tar.gz` archive is opened
- **THEN** `ar.cost.listing_cost == ListingCost.ON`
- **AND** `ar.cost.access_cost == AccessCost.SOLID`

#### Scenario: solid 7z exposes block count

- **WHEN** a solid 7z archive with multiple solid folders is opened
- **THEN** `ar.cost.is_solid == True`
- **AND** `ar.cost.solid_block_count` equals the number of distinct solid blocks in the archive
