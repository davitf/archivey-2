# Access Mode and Cost

## Purpose

Allows callers to declare upfront how they intend to access an archive (sequential vs. random), and exposes a machine-readable `CostReceipt` describing the actual listing cost, per-member access cost, stream seekability, and solid-block count. Together these two mechanisms let callers make informed access decisions and let the library enforce contracts that prevent accidentally expensive operations.

## Requirements

### Requirement: Declaring access mode at open_archive()

The system SHALL accept a `streaming: bool` parameter in `archivey.open_archive()` (default `False`) by which the caller declares their access pattern. The library uses it to optimize backend initialization and to enforce access constraints. There are exactly two modes:

- **`streaming=False`** (default) — **random access**. The library loads index structures (central directories, 7z headers) when available, and presents the archive for arbitrary member access. It **requires a source it can random-access**: it fails fast at `open_archive()` if the source is non-seekable and the format cannot adapt (it does **not** silently degrade to forward-only, which would surface failures only later, at read time). For seekable single-stream formats, seek points are built **lazily** — only if the caller actually `seek()`s.
- **`streaming=True`** — **forward-only, single pass**. The caller promises one forward pass. The library MUST disable index loading where possible, avoiding the upfront cost of scanning or parsing a central directory, and works on any source (including non-seekable pipes/sockets). All random-access and full-materialization operations are disabled **uniformly** — independent of whether a backend happens to have an index loaded — so `streaming=True` behaviour is deterministic across formats. `get_members_if_available()` stays callable because it never scans.

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

---

### Requirement: Access-mode enforcement — streaming is forward-only

A reader opened with `streaming=True` is forward-only. The system SHALL raise `UnsupportedOperationError` from every random-access or full-materialization method — `members()`, `get()`, `open()`, and `read()`. (`ArchiveReader` defines no `__len__`/`__getitem__` at all — see `archive-reading` — and `member in reader` is scan-free identity membership, allowed in both modes.) This holds **uniformly**, regardless of whether the backend happens to have an index loaded, so streaming behaviour does not vary by format.

The source is traversed **at most once**, forward. The system SHALL treat `__iter__`, `stream_members`, and `extract_all` as the forward-pass entry points: the first of them to run consumes the single pass, and any subsequent call to **any** of them SHALL raise `UnsupportedOperationError` — uniformly for every format, and even after the first pass ran to completion (there is **no** cache-replay of `__iter__` in streaming mode; a caller that wants the list again uses `scan_members()` or `get_members_if_available()`). A pass abandoned before EOF (an early `break`) still counts as consumed. (There is no single-member `extract()`; selecting members for extraction is `extract_all(members=...)` — see `safe-extraction`.)

`scan_members()` is the sole exception: it MAY run before the pass (initiating and finishing it), after an *interrupted* pass (finishing the remainder internally), or after a completed pass (returning the cache). It returns the fully-resolved member list. When it initiates the pass it also consumes it, so a later `__iter__`/`stream_members`/`extract_all` SHALL raise.

`get_members_if_available()` neither begins nor advances the forward pass and never marks it consumed (see the next requirement), so it remains callable on any reader at any time.

#### Scenario: random access raises on a streaming reader

- **WHEN** any of `ar.get("f")`, `ar.members()`, `ar.open(m)`, or `ar.read(m)` is called on a reader opened with `streaming=True`
- **THEN** `UnsupportedOperationError` is raised

#### Scenario: a single forward pass is allowed on a streaming reader

- **WHEN** a `streaming=True` reader is iterated once via `__iter__` or `stream_members()`
- **THEN** members are yielded in archive order without error

#### Scenario: a second forward pass raises uniformly, even after completion

- **WHEN** a `streaming=True` reader has begun or completed one forward pass (via `__iter__`, `stream_members`, or `extract_all`) and any of those forward-pass methods is called again
- **THEN** `UnsupportedOperationError` is raised, regardless of format and regardless of whether the first pass ran to completion

#### Scenario: scan_members() finishes an interrupted pass

- **WHEN** a `streaming=True` reader's `__iter__` (or `stream_members`) is interrupted with an early `break`, then `ar.scan_members()` is called
- **THEN** `scan_members()` drains the remainder of the single pass internally and returns the complete, fully-resolved member list
- **AND** a subsequent `stream_members()` / `__iter__` / `extract_all` raises `UnsupportedOperationError`

#### Scenario: scan_members() before any pass consumes it

- **WHEN** `ar.scan_members()` is called on a not-yet-iterated `streaming=True` reader and afterwards `ar.stream_members()` is called
- **THEN** `scan_members()` returns the full member list, and the subsequent `stream_members()` raises `UnsupportedOperationError`, for every index topology (leading, trailing, no-index)

---

### Requirement: get_members_if_available() — an index-only member list

The system SHALL provide `get_members_if_available() -> list[ArchiveMember] | None`. It is **index-only**: it performs **no forward scan and no member-data reads**, and never begins or consumes the forward pass, so it is safe to call on any reader (including `streaming=True`) at any time without affecting a later pass. It returns the full member list when that list is available from a true upfront index or an already-materialized cache (a completed iteration / `scan_members` pass, or `members()` in random mode), and `None` otherwise. A caller that wants a guaranteed-materialized, fully-resolved list uses `members()` (random-access mode) or `scan_members()` (either mode).

Availability depends on the format's **index topology** (the `_MEMBER_LIST_UPFRONT` predicate):

- **Leading-index** (directory listing, ISO): reachable from the front — available in both modes.
- **Trailing-index** (ZIP central directory, native 7z header at EOF): reachable **only by seeking to the end**, so availability presupposes a **seekable source**. Those backends require a seekable source in every mode (they do not permit non-seekable streaming -- `SUPPORTS_STREAMING_NON_SEEKABLE` is false), so their list is available in both modes. A hypothetical future format with a trailing index that also permitted non-seekable streaming SHALL report unavailable (`None`) on a non-seekable source.
- **No-index** (TAR): not reachable index-only — `None` until a forward pass has completed (or `scan_members()`/`members()` materialized the cache), after which the materialized, fully-resolved list is returned.

Because it is index-only, the members it returns are **not guaranteed to have resolved links**: for a format whose link *targets* are stored in member data (e.g. a ZIP symlink's target is its file content), `get_members_if_available()` SHALL return those members with `link_target` and `link_target_member` **unset**, since resolving them would require reading member data. `members()` and `scan_members()` perform the reads/scan needed to resolve links; `get_members_if_available()` does not.

#### Scenario: indexed backend returns the list even on a streaming reader

- **WHEN** `ar.get_members_if_available()` is called on a `streaming=True` reader of a format with an upfront index (e.g. ZIP)
- **THEN** the full member list is returned, with no scan and no member-data read, and the single forward pass remains available

#### Scenario: streaming backend returns None before iteration

- **WHEN** `ar.get_members_if_available()` is called on a not-yet-iterated reader of a no-index format (e.g. a streaming tar)
- **THEN** `None` is returned

#### Scenario: no-index backend returns the resolved list after a completed pass

- **WHEN** a `streaming=True` reader of a no-index format is iterated to completion (or `scan_members()` is called), then `ar.get_members_if_available()` is called
- **THEN** the fully-resolved materialized list is returned rather than `None`

#### Scenario: index-only listing leaves data-stored link targets unresolved

- **WHEN** `ar.get_members_if_available()` is called on a ZIP archive containing a symlink (whose target is stored in the member's data)
- **THEN** the returned symlink member has `link_target` and `link_target_member` unset (no member-data read occurs)
- **AND** `ar.members()` / `ar.scan_members()` on the same archive return that symlink with its `link_target` populated and `link_target_member` resolved

---

### Requirement: Access mode × method behaviour summary

The per-method behaviour is the composition of the rules above. There are exactly two modes: **random access** (`streaming=False`, the default) and **streaming** (`streaming=True`). The system SHALL behave per this table (`✅` = allowed, `⛔` = `UnsupportedOperationError`):

| Method | random access (`streaming=False`) | streaming (`streaming=True`) |
|--------|-----------------------------------|------------------------------|
| `__iter__` | ✅ (repeatable; from cache after first) | ✅ **once** (no replay; second call ⛔) |
| `stream_members` | ✅ | ✅ once (the one pass; second call ⛔) |
| `extract_all` | ✅ | ✅ once (the one pass) |
| `scan_members` | ✅ (= `members`) | ✅ (finishes the pass; may follow an interrupted/completed one) |
| `get_members_if_available` | ✅ (index-only; may be `None`) | ✅ (index-only, no-consume; may be `None`) |
| `members` | ✅ (may scan) | ⛔ |
| `get` | ✅ (may scan) | ⛔ |
| `open`, `read` | ✅ | ⛔ |
| `in` (`__contains__`, identity — see `archive-reading`) | ✅ (no scan) | ✅ (no scan) |
| `cost`, `info`, `format`, `close`, context manager | ✅ | ✅ |
| at `open_archive()` | fail fast if the source can't be random-accessed | works on any source |

In streaming mode, `__iter__`, `stream_members`, and `extract_all` all draw on the **same single forward pass**: whichever runs first consumes it, and a later one raises; `scan_members()` may still finish or return that pass's result. The independent backend-capability flag `_SUPPORTS_RANDOM_ACCESS` can also force `open`/`read` to raise (a backend that cannot seek the source at all); it composes with — does not replace — the access-mode rules above.

#### Scenario: scan_members is allowed in both modes

- **WHEN** `ar.scan_members()` is called on either a `streaming=False` or a `streaming=True` reader
- **THEN** it returns the fully-resolved member list (in random-access mode it is equivalent to `members()`; in streaming mode it finishes/consumes the single forward pass)

#### Scenario: streaming __iter__ does not replay after completion

- **WHEN** a `streaming=True` reader is fully iterated once via `__iter__`, then iterated again
- **THEN** the second iteration raises `UnsupportedOperationError` (streaming `__iter__` is single-use; use `scan_members()` / `get_members_if_available()` for the list)

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
