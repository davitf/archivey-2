# Access Mode and Cost — delta (concurrent-open-opt-in)

## MODIFIED Requirements

### Requirement: Declaring access mode at open_archive()

The system SHALL accept a `streaming: bool` parameter in `archivey.open_archive()` (default `False`) by which the caller declares their access pattern. The library uses it to optimize backend initialization and to enforce access constraints. There are exactly two modes:

- **`streaming=False`** (default) — **random access**. The library loads index structures (central directories, 7z headers) when available, and presents the archive for arbitrary member access. It **requires a source it can random-access**: it fails fast at `open_archive()` if the source is non-seekable and the format cannot adapt (it does **not** silently degrade to forward-only, which would surface failures only later, at read time). For seekable single-stream formats, seek points are built **lazily** — only if the caller actually `seek()`s.
- **`streaming=True`** — **forward-only, single pass**. The caller promises one forward pass. The library MUST disable index loading where possible, avoiding the upfront cost of scanning or parsing a central directory, and works on any source (including non-seekable pipes/sockets). All random-access and full-materialization operations are disabled **uniformly** — independent of whether a backend happens to have an index loaded — so `streaming=True` behaviour is deterministic across formats. `get_members_if_available()` stays callable because it never scans.

A separate operational keyword, `allow_multiple_open_streams: bool = False` (see
`archive-reading`, *Opening an archive for reading*), **composes with** access mode: it is
meaningful only in random-access mode (`streaming=False`), where it lifts the default limit of
one live member stream at a time. It does not change the two access modes above, and it is not
absorbed into the config object. Its gate is enforced **uniformly across all formats** — like
streaming-mode enforcement — so concurrent-open behaviour is deterministic regardless of the
archive's format or cost.

> A caller who wants forward-only access to a non-seekable source passes `streaming=True`; a caller who needs random access over a non-seekable source must buffer it (e.g. to a file or `BytesIO`) and reopen, rather than relying on implicit buffering. *(Eager seek-point building — the old `Intent.RANDOM` promise — is intentionally not exposed; it can return later as an explicit opt-in flag if a need arises.)*

#### Scenario: random-access (default) open on an indexed format

- **WHEN** `archivey.open_archive("archive.zip")` is called (`streaming=False`)
- **THEN** the ZIP central directory is read upfront and random access is available

#### Scenario: streaming open disables index loading

- **WHEN** `archivey.open_archive("archive.tar.gz", streaming=True)` is called
- **THEN** the library does not attempt to scan the full archive to build an index, and members are yielded as the stream is read

#### Scenario: random-access (default) fails fast on a non-seekable source

- **WHEN** `archivey.open_archive(non_seekable_stream)` is called (`streaming=False`) on a format that needs to seek
- **THEN** an appropriate error is raised at open time, before any member data is read — the caller must pass `streaming=True` (or buffer the source) to proceed

#### Scenario: the concurrent-open flag composes with random-access mode

- **WHEN** `archivey.open_archive(source, streaming=False, allow_multiple_open_streams=True)` is called
- **THEN** the reader is in random-access mode and additionally permits holding several member streams open at once; passing the flag with `streaming=True` has no effect (a single forward pass never holds multiple streams open)

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

**`access_cost` is informational, not a gate.** It describes whether reading (and, when the
caller has opted in via `allow_multiple_open_streams`, interleaving) member data is cheap
(`DIRECT`) or potentially expensive (`SOLID` — a re-decompress per rewind). The library SHALL
NOT use `access_cost` to decide whether concurrent open is *permitted*: the concurrent-open gate
is format-uniform (see `archive-reading`), so legality never varies by cost. `access_cost` /
`solid_block_count` are the signal an opted-in caller consults to decide whether interleaving is
worth its cost.

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

#### Scenario: access_cost does not gate concurrent open

- **WHEN** a reader is opened with `allow_multiple_open_streams=True` on a `SOLID` archive and again on a `DIRECT` archive
- **THEN** both permit interleaved concurrent opens (legality is format-uniform); `access_cost` only indicates that the `SOLID` case may re-decompress while the `DIRECT` case does not

#### Scenario: stream capability reflects the source, not the format

- **WHEN** the same plain `.tar` is opened once from a seekable file and once from a non-seekable pipe
- **THEN** `ar.cost.stream_capability` is `SEEKABLE` in the first case and `FORWARD_ONLY` in the second
- **AND** `ar.cost.access_cost` is `DIRECT` in both, because it describes the format layout, not the source

#### Scenario: solid 7z exposes block count

- **WHEN** a solid 7z archive with multiple solid folders is opened
- **THEN** `ar.info.is_solid == True`
- **AND** `ar.cost.access_cost == AccessCost.SOLID`
- **AND** `ar.cost.solid_block_count` equals the number of distinct solid blocks in the archive
