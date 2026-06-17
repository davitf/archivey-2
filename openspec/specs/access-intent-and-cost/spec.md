# Access Intent and Cost

## Purpose

Allows callers to declare upfront how they intend to access an archive (sequential vs. random), and exposes a machine-readable `CostReceipt` describing the actual listing cost, per-member access cost, stream seekability, and solid-block count. Together these two mechanisms let callers make informed access decisions and let the library enforce contracts that prevent accidentally expensive operations.

## Requirements

### Requirement: Declaring access intent at open_archive()

The system SHALL accept an `intent` parameter in `archivey.open_archive()` that the caller uses to declare their intended access pattern. The library uses this declaration to optimize backend initialization and to enforce access constraints.

```python
class Intent(Enum):
    AUTO       = "auto"       # library chooses optimal access mode
    SEQUENTIAL = "sequential" # caller promises forward-only iteration; disables index loading
    RANDOM     = "random"     # caller needs random access; library fails fast if impossible
```

- `Intent.AUTO`: the library selects the most appropriate mode for the detected format. Index structures (central directories, 7z headers) are loaded when available. For seekable single-stream formats, seek points (the index that makes random access into a compressed stream affordable) are **not** built up front; the library builds them **lazily** — only if the caller actually `seek()`s, and only when the backend judges it worthwhile.
- `Intent.SEQUENTIAL`: the caller promises forward-only, single-pass iteration. The library MUST disable index loading where possible, avoiding the upfront cost of scanning or parsing a central directory. Random-access operations (`__getitem__`, `get`, random `extract`) are disabled on sequential readers unless the backend can satisfy them cheaply via an already-loaded in-memory index.
- `Intent.RANDOM`: the caller requires random member access. The library SHALL fail fast at `open_archive()` time if the format or source cannot support random access (e.g. a non-seekable stream for a format that requires seek). It also signals that random access is expected, so the backend MAY **proactively** build seek points for compressed single-stream formats rather than deferring them.

#### Scenario: AUTO intent on an indexed format

- **WHEN** `archivey.open_archive("archive.zip", intent=Intent.AUTO)` is called
- **THEN** the ZIP central directory is read upfront and random access is available

#### Scenario: SEQUENTIAL intent disables index loading

- **WHEN** `archivey.open_archive("archive.tar.gz", intent=Intent.SEQUENTIAL)` is called
- **THEN** the library does not attempt to scan the full archive to build an index, and members are yielded as the stream is read

#### Scenario: RANDOM intent fails fast on non-seekable source

- **WHEN** `archivey.open_archive(non_seekable_stream, intent=Intent.RANDOM)` is called on a format that requires seek
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

The system SHALL compute and expose a `CostReceipt` for every opened archive, available via `ar.cost` and embedded in `ar.info.cost`. The receipt SHALL be computed during `open_read()` before any heavy I/O. It is the most subtle part of the API, so each field is defined precisely below; the receipt describes three **independent** axes plus a solid-block count.

```python
class ListingCost(Enum):
    """How expensive it is to ENUMERATE all members (list names + metadata)."""
    INDEXED               = "indexed"               # an index / central directory is present;
                                                    #   listing is O(1) regardless of archive size
    REQUIRES_SCANNING     = "requires_scanning"     # no index, but members can be enumerated by
                                                    #   seeking/scanning header-to-header without
                                                    #   decompressing payload (e.g. uncompressed tar,
                                                    #   or a RAR with no quick-open record)
    REQUIRES_DECOMPRESSION = "requires_decompression" # the stream must be decompressed to reach the
                                                    #   member headers (e.g. a compressed tar)

class AccessCost(Enum):
    """How expensive it is to READ one member's data, given the FORMAT layout."""
    DIRECT = "direct"   # any member can be read without touching other members
    SOLID  = "solid"    # reading member N may require decompressing earlier members in its block

class StreamCapability(Enum):
    """A property of the underlying SOURCE bytes, independent of the format layout."""
    SEEKABLE     = "seekable"      # the source supports arbitrary seek(); positions can be revisited
    FORWARD_ONLY = "forward_only"  # non-seekable source (pipe/socket): it cannot be rewound at all.
                                   #   Re-reading any earlier position requires a brand-new stream.

@dataclass(frozen=True)
class CostReceipt:
    listing_cost: ListingCost
    access_cost: AccessCost
    stream_capability: StreamCapability
    solid_block_count: int | None   # number of distinct solid blocks (each one decompress pass),
                                    #   or None when not applicable / unknown. is_solid lives on
                                    #   ArchiveInfo, not here, to avoid duplicating the flag.
    notes: tuple[str, ...] = ()     # human-readable caveats
```

**The three axes are orthogonal and MUST NOT be conflated:**

- `stream_capability` is about the **source byte stream** — can the raw bytes be
  `seek()`ed? A file on disk is `SEEKABLE`; a socket or pipe is `FORWARD_ONLY`. A
  `FORWARD_ONLY` source cannot be rewound at all — not even to re-read an earlier
  member — so anything requiring a revisit needs a fresh stream.
- `access_cost` is about the **format layout** — is member N's data independent
  (`DIRECT`) or entangled with earlier members in a shared compression stream
  (`SOLID`)? This is where "rewinding a decompressed stream costs a re-decompress from
  the block start" belongs — it is a consequence of `SOLID` (and of `ArchiveInfo.is_solid`),
  *not* of source seekability.
- `listing_cost` is about **enumeration** — getting names+metadata for all members.

They compose. Examples:

- **ZIP** on a file: `INDEXED` (EOCD/central directory) + `DIRECT` (per-member offsets) + `SEEKABLE`.
- **plain `.tar`** on a file: `REQUIRES_SCANNING` (walk 512-byte headers, no decompress) + `DIRECT` + `SEEKABLE`.
- **plain `.tar`** on a pipe: `REQUIRES_SCANNING` + `DIRECT` + `FORWARD_ONLY` (one forward pass only).
- **`.tar.gz`** on a file: `REQUIRES_DECOMPRESSION` (must inflate to reach headers) + `SOLID` (single gzip stream) + `SEEKABLE` (the *source* seeks, even though random member access still costs a re-decompress).
- **7z** solid: `INDEXED` (header block at start) + `SOLID` (members share folders) + `SEEKABLE`, with `solid_block_count` = number of solid folders.

#### Scenario: CostReceipt available immediately after open

- **WHEN** an archive is opened successfully
- **THEN** `ar.cost` is populated without requiring a separate scan or read of member data

#### Scenario: ZIP reports INDEXED listing cost and DIRECT access

- **WHEN** a ZIP archive is opened
- **THEN** `ar.cost.listing_cost == ListingCost.INDEXED`
- **AND** `ar.cost.access_cost == AccessCost.DIRECT`

#### Scenario: compressed tar requires decompression to list and is SOLID

- **WHEN** a `.tar.gz` archive is opened
- **THEN** `ar.cost.listing_cost == ListingCost.REQUIRES_DECOMPRESSION`
- **AND** `ar.cost.access_cost == AccessCost.SOLID`

#### Scenario: stream capability reflects the source, not the format

- **WHEN** the same plain `.tar` is opened once from a seekable file and once from a non-seekable pipe
- **THEN** `ar.cost.stream_capability` is `SEEKABLE` in the first case and `FORWARD_ONLY` in the second
- **AND** `ar.cost.access_cost` is `DIRECT` in both, because it describes the format layout, not the source

#### Scenario: solid 7z exposes block count

- **WHEN** a solid 7z archive with multiple solid folders is opened
- **THEN** `ar.info.is_solid == True`
- **AND** `ar.cost.access_cost == AccessCost.SOLID`
- **AND** `ar.cost.solid_block_count` equals the number of distinct solid blocks in the archive
