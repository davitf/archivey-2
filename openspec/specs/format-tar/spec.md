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

### Requirement: Detect truncated TAR archives

The system SHALL verify archive integrity at the end of iteration by checking for valid end-of-archive markers.

After iterating all members, the system verifies that the final 512-byte block(s) are null-filled end-of-archive markers. Strictness is configured by `ArchiveyConfig.strict_archive_eof` (see `archive-reading`; the Phase 4 `open_archive(strict_eof=)` keyword is removed). If the markers are absent:

- By default (`config.strict_archive_eof=False`): emit a `logging.WARNING` via the `archivey.backends.*` logger.
- When `config.strict_archive_eof=True`: raise `TruncatedError`.

#### Scenario: Valid TAR end-of-archive markers present

- **WHEN** all TAR members have been iterated
- **AND** the archive ends with null-filled 512-byte end-of-archive block(s)
- **THEN** no warning or error is emitted

#### Scenario: Missing end-of-archive markers, default mode

- **WHEN** all TAR members have been iterated
- **AND** the archive does not end with valid null-filled end-of-archive block(s)
- **AND** `config.strict_archive_eof` is `False` (the default)
- **THEN** the system emits a `logging.WARNING` indicating the archive may be truncated

#### Scenario: Missing end-of-archive markers, strict mode

- **WHEN** all TAR members have been iterated
- **AND** the archive does not end with valid null-filled end-of-archive block(s)
- **AND** `config.strict_archive_eof` is `True`
- **THEN** the system raises `TruncatedError`

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

