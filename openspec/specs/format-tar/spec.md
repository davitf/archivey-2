# TAR Format Behavior

## Purpose

The TAR backend presents all TAR variants (plain `.tar`, and compressed `.tar.gz`, `.tar.bz2`, `.tar.xz`, `.tar.zst`) through the unified `ArchiveReader` / `ArchiveWriter` interface using Python's stdlib `tarfile` module. It reads sequentially with no central directory, supports streaming writes, and handles TAR-specific semantics including PAX extended headers, hardlink two-pass extraction, and truncation detection.
## Requirements
### Requirement: Report TAR format properties

The system SHALL expose the following cost and capability properties for every opened TAR archive:

| Property | Value |
|----------|-------|
| Backend dependency | `tarfile` (stdlib) |
| Listing cost | No central directory: `REQUIRES_DECOMPRESSION` for compressed tars (must inflate to reach headers), `REQUIRES_SCANNING` for plain `.tar` (walk 512-byte headers, no decompress) |
| Access cost | SOLID for `.tar.gz`, `.tar.bz2`, `.tar.xz`, `.tar.zst`; DIRECT for plain `.tar` |
| Supports write | Yes |
| Requires seek | No (streaming mode) |

#### Scenario: CostReceipt for compressed TAR

- **WHEN** an archive with format `TAR_GZ`, `TAR_BZ2`, `TAR_XZ`, or `TAR_ZST` is opened
- **THEN** `cost.access_cost` is `AccessCost.SOLID` and `cost.listing_cost` is `ListingCost.REQUIRES_DECOMPRESSION`

#### Scenario: CostReceipt for plain TAR

- **WHEN** an archive with format `TAR` (plain, uncompressed) is opened
- **THEN** `cost.access_cost` is `AccessCost.DIRECT` and `cost.listing_cost` is `ListingCost.REQUIRES_SCANNING`

### Requirement: Map TAR member metadata to the unified ArchiveMember model

The system SHALL map each `TarInfo` entry to a `ArchiveMember` dataclass using the following field rules:

- `mode`: from `TarInfo.mode` (lower 12 bits).
- `modified`: from `TarInfo.mtime` (Unix timestamp), interpreted as UTC and returned as a timezone-aware `datetime`.
- PAX extended headers (`pax_headers`) override `mtime` with full precision and optional timezone information when present.
- `uname`, `gname`, `uid`, `gid`: taken directly from the corresponding `TarInfo` fields.
- `type`: mapped from the TAR type byte (`REGTYPE`, `DIRTYPE`, `SYMTYPE`, `LNKTYPE`, etc.) to the corresponding `MemberType` value.

#### Scenario: PAX header overrides mtime

- **WHEN** a TAR member carries PAX extended headers that include an `mtime` field
- **THEN** `member.modified` is derived from the PAX `mtime` value (which may carry sub-second precision and timezone information), overriding the value from `TarInfo.mtime`

#### Scenario: Standard mtime mapping

- **WHEN** a TAR member carries no PAX `mtime` override
- **THEN** `member.modified` is a timezone-aware UTC `datetime` constructed from `TarInfo.mtime` (Unix timestamp)

#### Scenario: Hardlink type mapping

- **WHEN** a TAR entry has type byte `LNKTYPE`
- **THEN** `member.type` is `MemberType.HARDLINK` and `member.link_target` is set to the `linkname` field

### Requirement: Handle TAR hardlinks via two-pass extraction with cross-device fallback

The system SHALL support hardlink extraction from TAR archives; the `linkname` field holds
the source path, and TAR ordering guarantees the real file (the source) precedes any hardlink
that references it. Resolution is performed by the `safe-extraction` `ExtractionCoordinator`,
which acts as a **pull-based sink**: it drives the `ArchiveReader` — iterating the forward
pass, calling `reader.get_members_if_available()` for the optional optimization, and, only on
an orphan, checking whether the source can be re-read for a second pass. It does **not** need
the `SOLID`/`DIRECT` axis of `reader.cost`. It MUST NOT hold a push-model deferred-state
machine, and MUST NOT force an upfront pass the run does not need.

Only a `members` selector or a `filter` can **orphan** a hardlink — select a link while
excluding its source; an unfiltered extract-all never orphans one, because the source is
always selected and precedes the link. The coordinator uses **one core algorithm**, with an
**optional optimization** when a free member list is available:

**Core — sequential pass with a conditional second pass.** One forward pass: write each
selected member; record each written FILE under a per-source **list of on-disk paths**; a
selected hardlink to an already-written source is created with `os.link()`. This alone handles
the common case — with no filter no link is ever orphaned, so the pass completes in one go
with no second pass. If a `filter`/selector *does* orphan a selected link (its source was
excluded), the coordinator:

- on a **re-readable** source (seekable / random-access), collects the orphans and resolves
  them in a **single second pass** afterwards — only when at least one orphan exists. For a
  plain `.tar` the second pass re-scans headers; for a compressed tar it re-decompresses (so
  the stream is decompressed at most twice, and exactly once when there are no orphans).
- on a **forward-only** source (a `streaming=True` reader that cannot be re-read), the source's
  bytes are unrecoverable — a per-member failure handled by the configured `OnError` policy
  (STOP raises `ExtractionError`; CONTINUE records a `FAILED` `ExtractionResult` and proceeds).

The coordinator SHALL NOT speculatively call `members()` to look ahead: a header scan of a
plain `.tar` is not reliably cheap (seek-heavy on spinning or network media), and listing a
compressed tar would decompress the whole stream. It pays the second pass only when an orphan
actually forces it.

**Optional optimization — planned single pass.** When a selector/`filter` is in use **and** a
member list is available *for free* — `get_members_if_available()` returns it (a true central
directory, or an already-materialized list) — the coordinator MAY plan up front (apply the
selector, policy transform and `filter` to the list, computing the write plan and a
`source → selected-link-paths` map) and, in the single forward pass, write each **needed**
source to the first selected link's path even if the source itself was excluded (its bytes are
streaming past regardless), `os.link()`ing the remaining selected links. This avoids the second
pass for indexed sources. It is an optimization layered on the core algorithm, not a separate
correctness path.

**Cross-device handling.** The coordinator keeps, per source, the **list of on-disk paths** it
has already created for that source's content. To create a new link it tries `os.link()`
against each recorded path in turn; the first that succeeds wins. If every attempt fails with a
cross-device error (`EXDEV`), it falls back to `shutil.copy2` from an existing copy and appends
the new path to the source's list. This automatically handles chained links: `B → A` landing
cross-device copies `A`'s content to `B`; a later `C → A` on `B`'s device then succeeds with
`os.link(B, C)` (the first attempt against `A` fails `EXDEV`, the attempt against `B` works).
No filesystem-device bookkeeping is required — an implementation MAY consult
`os.stat(path).st_dev` (and `os.stat(dest.parent).st_dev` for the destination) as an
optimization to skip attempts doomed to `EXDEV`, but it is not needed for correctness. This is
strictly better than `tarfile`, which re-extracts the source's data from the archive for every
cross-device link and never links sibling copies to each other.

The excluded source's own name is never created on disk. The only auxiliary structures are
the per-source list of on-disk paths, a bounded list of orphaned links awaiting the second
pass (core algorithm), and the write plan (optional optimization); none of these is a
push-model deferred-creation machine.

#### Scenario: Unfiltered extract resolves hardlinks in one sequential pass

- **WHEN** an archive is extracted with no `members` selector or `filter` that excludes a hardlink source
- **THEN** every hardlink resolves during a single sequential pass with `os.link` (source precedes link) and no member list is fetched up front

#### Scenario: Filtered extract with a free member list stages orphaned sources in one pass

- **WHEN** a `filter` excludes a hardlink's source but selects the link, and `get_members_if_available()` returns the list (a true index or an already-materialized list)
- **THEN** the coordinator plans up front, and during the single forward pass writes the excluded source's content to the first selected link's path (further selected links `os.link` to it); no second pass is used and the excluded source is never created at its own path

#### Scenario: Filtered tar without a free list recovers an orphan in one second pass

- **WHEN** a plain `.tar` or a `.tar.gz` (no free list) is extracted with a filter that orphans one or more hardlinks on a seekable source
- **THEN** the coordinator does not speculatively scan/list up front, and resolves all orphans in a single second pass after the main pass (a compressed tar is thus decompressed at most twice)

#### Scenario: Filtered tar with no orphan does not take a second pass

- **WHEN** a plain `.tar` or `.tar.gz` is extracted with a filter that does not orphan any hardlink
- **THEN** extraction completes in a single pass with no second pass and no up-front list fetch

#### Scenario: Orphaned link on a forward-only source follows OnError

- **WHEN** a selected hardlink whose source was excluded is reached on a forward-only (streaming) source
- **THEN** it is a per-member failure: `OnError.STOP` raises `ExtractionError`, and `OnError.CONTINUE` records a `FAILED` `ExtractionResult` and continues

#### Scenario: Chained cross-device links reuse a sibling copy

- **WHEN** `B → A` is written to a different device than `A` (forcing a `shutil.copy2` of `A` to `B`)
- **AND** a later `C → A` is written to the same device as `B`
- **THEN** `C` is created with `os.link(B, C)` rather than copying `A`'s content a second time

#### Scenario: Cross-device hardlink falls back to copy

- **WHEN** `os.link()` against every recorded on-disk path of the source fails with a cross-device error
- **THEN** the system copies the source content to the link destination and appends that path for reuse by later links on the same device

### Requirement: Invalid TAR timestamps are member diagnostic data

The existing TAR mapping remains. If `TarInfo.mtime` cannot be represented as a Python
`datetime`, the member's `modified` SHALL be `None` and the reader SHALL emit
`MEMBER_TIMESTAMP_INVALID` with member identity, field/source kind, and a JSON-safe value
representation. Under default policy the occurrence is collected/logged and MAY attach to
the member under the shared budget; under `RAISE`, listing halts with
`DiagnosticRaisedError`.

#### Scenario: invalid TAR mtime is member data

- **WHEN** a TAR member carries an out-of-range mtime
- **THEN** `modified is None`, `MEMBER_TIMESTAMP_INVALID` is counted on the reader, and its retained occurrence may attach to the member

### Requirement: Detect truncated TAR archives

After full iteration, a missing/invalid TAR end marker SHALL emit
`ARCHIVE_EOF_MARKER_MISSING` on the reader operation aggregate. It SHALL not attach to
`ArchiveInfo`, `CostReceipt`, or a member.

Its context SHALL be `ArchiveEofContext(kind="archive_eof", format="tar",
expected_marker="two_zero_blocks", expected_bytes=1024, observed_bytes=...,
observed_kind=...)` plus the best-effort archive display name. `observed_kind` SHALL be
`"absent"`, `"short"`, or `"nonzero"` as applicable; raw trailing archive bytes SHALL
not be retained.

The event first follows the common count/retention/log/callback order. Then:

- with `strict_archive_eof=False`, ordinary disposition applies (`IGNORE` continues,
  `COLLECT` continues, `RAISE` raises `DiagnosticRaisedError`);
- with `strict_archive_eof=True`, `TruncatedError` always halts and takes precedence over
  `DiagnosticRaisedError`, including when disposition is `IGNORE` or `RAISE`.

A logging-handler or callback exception propagates at its earlier ordered step.

#### Scenario: valid TAR marker has no event

- **WHEN** full iteration ends with valid null-filled end-of-archive marker blocks
- **THEN** no EOF diagnostic or error is produced

#### Scenario: missing marker collected by default

- **WHEN** a TAR pass reaches a missing EOF marker with default config
- **THEN** `ARCHIVE_EOF_MARKER_MISSING` is counted/retained/logged on the reader and the pass completes

#### Scenario: ignored missing marker is still strict

- **WHEN** the code resolves to `IGNORE` and `strict_archive_eof=True`
- **THEN** the event count increments without delivery and `TruncatedError` is raised

#### Scenario: strict error type wins over diagnostic raise

- **WHEN** the code resolves to `RAISE`, delivery succeeds, and `strict_archive_eof=True`
- **THEN** `TruncatedError` is raised after delivery rather than `DiagnosticRaisedError`

#### Scenario: runtime EOF event does not mutate open-time info

- **WHEN** the missing marker is discovered only after iteration
- **THEN** `reader.diagnostics` changes and frozen `ArchiveInfo` / `CostReceipt` remain unchanged

### Requirement: Detect TAR compression variant from magic bytes

The system SHALL detect the compression variant of a TAR archive from the magic bytes of its first bytes and map the result to the appropriate `tarfile` mode string.

Detected compression variants and their `tarfile` mode strings:

| Compression | `tarfile` mode |
|-------------|----------------|
| gzip | `r:gz` |
| bzip2 | `r:bz2` |
| xz / lzma | `r:xz` |
| auto-detect | `r:*` |
| none (plain) | `r:` |

#### Scenario: Compressed TAR opened in correct mode

- **WHEN** a `.tar.gz` file is opened
- **THEN** the `tarfile` backend is invoked with mode `r:gz`

#### Scenario: Plain TAR opened without decompression

- **WHEN** a plain `.tar` file is opened
- **THEN** the `tarfile` backend is invoked with mode `r:` (no decompression wrapper)

---

### Requirement: Random-access TAR concurrent member open via locked extractfile streams

The system SHALL support interleaved concurrent member data streams from one
**random-access** TAR reader (`streaming=False`) unconditionally, as required by
`concurrent-member-streams`. The reader MUST continue to obtain file member payloads
through `tarfile.extractfile` (preserving sparse and other stdlib behavior).

Each `TarReader` SHALL own one lock covering **every operation on the tarfile shared
handle**, including:

- `tarfile.open()` archive initialization and failure cleanup;
- `getmembers()` and its `_load()` / `next()` shared-handle seek/tell/read sequence;
- Archivey's direct strict-EOF `TarFile.fileobj.read()`;
- `extractfile()` member creation;
- member `read` and `readinto`, plus `seek`/`tell` where supported;
- member close;
- archive/TarFile close; and
- any other operation found by audit to reposition or close `TarFile.fileobj`.

The lock surrounds the complete library operation, not separate raw seek/read calls.
Archivey buffering/error/lifecycle wrappers SHALL sit outside the locked layer, so buffer
refills cannot bypass it. Exception translation/stamping, logging, lifecycle lease
release, callbacks, and finalizer hooks SHALL run after the lock is released. Library-
internal decode inseparable from a shared-handle call MAY execute under the lock.
Unsupported positioning SHALL retain normal `io.UnsupportedOperation` behavior.

**Compressed TAR.** Concurrent open does not change the access-cost model: a compressed
TAR remains a single compression stream (`SOLID`). The lock guarantees correctness but
may serialize member operations and does not promise parallel throughput.

**Out of scope.** Streaming TAR (`streaming=True` / `r|`) remains single-pass. Replacing
tarfile with a native reader or serving members via shared-source views at `offset_data`
is not required by this capability.

#### Scenario: interleaved opens on plain TAR-RA

- **WHEN** two file members of a plain random-access TAR are opened and read interleaved
- **THEN** each stream yields that member's exact bytes in order

#### Scenario: interleaved opens on compressed TAR-RA

- **WHEN** two file members of a compressed random-access TAR (e.g. `.tar.gz`) are opened
  and read interleaved
- **THEN** each stream yields that member's exact bytes in order

#### Scenario: initialization and seek operations share the same lock

- **WHEN** workers concurrently create member streams, perform read/readinto, and use
  supported positioning
- **THEN** each complete tarfile operation is serialized by the same per-reader lock, so
  no member observes another member's file position

#### Scenario: materialization and EOF verification cover their handle I/O

- **WHEN** random-access TAR materialization calls `getmembers()` and then performs strict
  EOF verification
- **THEN** the complete tarfile scan calls and direct EOF `fileobj.read()` use the same
  per-reader handle lock

#### Scenario: callbacks run after releasing the TAR handle lock

- **WHEN** a TAR member operation raises or closes and archivey translates/logs/releases
  its lifecycle lease
- **THEN** that diagnostic/lifecycle work executes without the TAR shared-handle lock held

#### Scenario: sparse members still expand correctly

- **WHEN** a GNU sparse file member is opened from a random-access TAR
- **THEN** the stream yields the same logical bytes as before this change (stdlib sparse
  handling is preserved)

#### Scenario: streaming TAR contract is unchanged

- **WHEN** a TAR archive is opened with `streaming=True`
- **THEN** the reader remains forward-only and gains no concurrent-open seam
- **AND** its shared-handle calls still use the same normally uncontended backend lock

#### Scenario: TAR lock is a correctness mechanism, not a speed claim

- **WHEN** concurrent TAR member operations contend on one shared handle
- **THEN** correctness is guaranteed even if operations serialize
- **AND** a proportionate baseline records wall/lock timing and practical seek/byte counters
  without imposing a correctness speed threshold

