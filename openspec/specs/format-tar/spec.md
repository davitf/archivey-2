# TAR Format Behavior

## Purpose

TAR archives (`.tar`, `.tar.gz`, `.tar.bz2`, `.tar.xz`, `.tar.zst`) are read
and written through the unified archive APIs using stdlib `tarfile`. TAR has no
central directory: listing walks headers sequentially, compressed variants are
solid streams, and extraction preserves TAR-specific hardlink and EOF semantics.

## Related specs

| Spec | Relationship |
| --- | --- |
| `archive-reading` | Reader API, link-following semantics, declared member-stream capabilities |
| `access-mode-and-cost` | Cost axes and streaming vs random-access method rules |
| `safe-extraction` | Pull-based extraction coordinator, `OnError`, hardlink outcomes |
| `diagnostics` | Timestamp and archive-EOF diagnostic values / policy |
| `reader-concurrency` | `MemberStreams.CONCURRENT`, operation ownership, lock boundaries |

## Requirements

### Requirement: Report TAR format properties

The TAR backend SHALL expose these properties for every opened TAR archive:

| Format | `tarfile` mode | Listing cost | Access cost |
| --- | --- | --- | --- |
| Plain `.tar` | `r:` | `REQUIRES_SCANNING` | `DIRECT` |
| `.tar.gz` | `r:gz` | `REQUIRES_DECOMPRESSION` | `SOLID` |
| `.tar.bz2` | `r:bz2` | `REQUIRES_DECOMPRESSION` | `SOLID` |
| `.tar.xz` | `r:xz` | `REQUIRES_DECOMPRESSION` | `SOLID` |
| `.tar.zst` | zstd-backed equivalent | `REQUIRES_DECOMPRESSION` | `SOLID` |
| Auto-detected TAR | `r:*` where needed | Based on detected compression | Based on detected compression |

The backend SHALL support writing TAR archives, including streaming writes.
Compressed variants remain solid even when the source is seekable: random member
opens may re-decompress earlier bytes, while `stream_members()` is the preferred
progressive path.

#### Scenario: TAR property matrix

| Case | Expected |
| --- | --- |
| Open `TAR` | `cost.listing_cost=REQUIRES_SCANNING`; `cost.access_cost=DIRECT`; mode `r:` |
| Open `TAR_GZ`, `TAR_BZ2`, `TAR_XZ`, or `TAR_ZST` | `cost.listing_cost=REQUIRES_DECOMPRESSION`; `cost.access_cost=SOLID`; matching decompressor mode |
| Open `.tar.gz` | `tarfile` invoked with gzip mode |
| Open plain `.tar` | No decompression wrapper |
| Stream write TAR | Member data is written in archive order without requiring a seekable destination |

### Requirement: Map TAR member metadata to ArchiveMember

The TAR backend SHALL map each `TarInfo` to `ArchiveMember` with these field
rules:

| Field | Mapping |
| --- | --- |
| `mode` | `TarInfo.mode` lower 12 bits |
| `modified` | `TarInfo.mtime` as timezone-aware UTC |
| PAX `mtime` | Overrides `TarInfo.mtime`, preserving sub-second precision / timezone information |
| `uname`, `gname`, `uid`, `gid` | Directly from `TarInfo` |
| `type` | TAR type byte (`REGTYPE`, `DIRTYPE`, `SYMTYPE`, `LNKTYPE`, etc.) to `MemberType` |
| hardlink target | `LNKTYPE` maps to `MemberType.HARDLINK`; `link_target` from `linkname` |

If `TarInfo.mtime` cannot be represented as a Python `datetime`, `modified`
SHALL be `None` and `MEMBER_TIMESTAMP_INVALID` SHALL be emitted with typed,
JSON-safe member identity and source/value context. Under default policy it is
collected/logged and may attach to the member; under `RAISE`, listing halts with
`DiagnosticRaisedError`.

#### Scenario: TAR metadata matrix

| Case | Expected |
| --- | --- |
| PAX `mtime` present | `member.modified` derives from PAX value, overriding `TarInfo.mtime` |
| No PAX `mtime` | `member.modified` is timezone-aware UTC from `TarInfo.mtime` |
| `LNKTYPE` entry | `member.type=MemberType.HARDLINK`; `member.link_target=linkname` |
| Out-of-range `mtime` | `modified is None`; `MEMBER_TIMESTAMP_INVALID` counted and may attach |
| Timestamp diagnostic resolves to `RAISE` | Listing halts with `DiagnosticRaisedError` |

### Requirement: Extract TAR hardlinks with a pull-based coordinator

The system SHALL support TAR hardlink extraction through the `safe-extraction`
coordinator as a pull-based sink: it drives the reader forward, may inspect
`members_report_if_available()` only when that report is free, and checks re-read
possibility only if an orphaned hardlink exists. It MUST NOT use a push-model
deferred-state machine, force an upfront scan, or depend on the
`SOLID`/`DIRECT` axis for correctness.

Only a `members` selector or `filter` can orphan a hardlink by selecting the link
while excluding its source. Unfiltered extract-all SHALL resolve TAR hardlinks in
one sequential pass because the source precedes the link.

The core algorithm SHALL write selected members in one forward pass, recording
every written FILE path per source. A selected hardlink to an already-written
source is created with `os.link()`. If a selector/filter orphans a selected link:

| Source capability | Behavior |
| --- | --- |
| Re-readable / random-access | Collect orphan links and resolve all of them in one second pass after the main pass; plain TAR re-scans headers, compressed TAR re-decompresses at most once more |
| Forward-only | Treat as a per-member failure under configured `OnError` (`STOP` raises `ExtractionError`; `CONTINUE` records `FAILED` and proceeds) |

When a free member list is available and a selector/filter is in use, the
coordinator MAY plan up front: apply selection/filter policy, write an excluded
source's bytes to the first selected link path while they stream past, and
`os.link()` remaining selected links to that staged path. This optimization SHALL
not create the excluded source at its own name and SHALL not replace the core
correctness path.

For cross-device links, the coordinator SHALL try `os.link()` against every
recorded on-disk path for the source. If all fail with `EXDEV`, it SHALL
`shutil.copy2` from an existing copy and append the new path for reuse. Chained
links on that device can then link to the sibling copy. Device bookkeeping MAY
skip doomed attempts but is not required for correctness.

#### Scenario: TAR hardlink extraction matrix

| Case | Expected |
| --- | --- |
| Unfiltered extract-all | Hardlinks resolve in one pass; no upfront member list fetch |
| Filter excludes source but selects link and a free member list exists | One planned pass writes source bytes to first selected link path; remaining links use `os.link`; source name not created |
| Filter orphans links on seekable plain/compressed TAR with no free list | No speculative scan; all orphans resolved in one second pass; compressed stream decompressed at most twice total |
| Filter does not orphan any link | Single pass; no second pass; no upfront list fetch |
| Orphaned link on forward-only source | Per-member failure follows `OnError` |
| `B -> A` copied cross-device, then `C -> A` on B's device | `C` is created with `os.link(B, C)` rather than copying A again |
| Every recorded path fails with `EXDEV` | Copy source content to link destination and record that path |

### Requirement: Detect truncated TAR archives

After full iteration, a missing or invalid TAR end marker SHALL emit
`ARCHIVE_EOF_MARKER_MISSING` on the reader operation aggregate and SHALL NOT
attach to `ArchiveInfo`, `CostReceipt`, or a member. Context SHALL be
`ArchiveEofContext(kind="archive_eof", format="tar",
expected_marker="two_zero_blocks", expected_bytes=1024, observed_bytes=...,
observed_kind=...)` plus best-effort archive display name. `observed_kind` SHALL
be `"absent"`, `"short"`, or `"nonzero"`; raw trailing bytes SHALL NOT be
retained.

The diagnostic first follows normal count/retention/log/callback ordering. With
`strict_archive_eof=False`, ordinary disposition applies. With
`strict_archive_eof=True`, `TruncatedError` SHALL halt after delivery and take
precedence over `DiagnosticRaisedError`, including when disposition is `IGNORE`
or `RAISE`. Logging-handler or callback exceptions propagate at their earlier
ordered step.

#### Scenario: TAR EOF matrix

| Case | Expected |
| --- | --- |
| Valid two-block null marker | No EOF diagnostic or error |
| Missing marker, default config | `ARCHIVE_EOF_MARKER_MISSING` counted/retained/logged; pass completes |
| Code resolves to `IGNORE` and `strict_archive_eof=True` | Count increments without delivery; `TruncatedError` raises |
| Code resolves to `RAISE`, delivery succeeds, `strict_archive_eof=True` | `TruncatedError` raises after delivery instead of `DiagnosticRaisedError` |
| Marker issue discovered after iteration | `reader.diagnostics` changes; frozen `ArchiveInfo` / `CostReceipt` stay unchanged |

### Requirement: Serialize shared tarfile handle operations for concurrent reads

For random-access TAR readers that allow concurrent member streams under
`MemberStreams.CONCURRENT`, the backend SHALL keep using `tarfile.extractfile`
for file payloads and SHALL serialize every operation that touches the shared
`tarfile` handle with one per-reader lock. This preserves stdlib behavior such
as sparse-file expansion while preventing races on the shared file position.

The lock SHALL cover archive initialization/failure cleanup, `getmembers()` /
`_load()` / `next()` scans, strict-EOF direct reads, `extractfile()` stream
creation, member `read` / `readinto` / supported `seek` / `tell`, member close,
archive close, and any audited operation that repositions or closes
`TarFile.fileobj`. The lock surrounds the complete library operation, not
individual raw seek/read calls. Archivey buffering/error/lifecycle wrappers sit
outside it; exception translation, diagnostics/logging, lifecycle release,
callbacks, and finalizers run after the lock is released. Unsupported
positioning retains normal `io.UnsupportedOperation` behavior.

Compressed TAR remains `SOLID`; locking guarantees correctness but not parallel
throughput. Streaming TAR (`streaming=True` / `r|`) remains one forward pass and
does not gain random concurrent open.

#### Scenario: TAR handle-lock matrix

| Case | Expected |
| --- | --- |
| Two file members opened and read interleaved from plain RA TAR | Each yields its exact bytes in order |
| Two file members opened and read interleaved from compressed RA TAR | Each yields exact bytes; serialization is acceptable |
| Multiple threads open/read distinct TAR members under `MemberStreams.CONCURRENT` after materialization | No data races on the shared handle |
| Materialization then strict EOF verification | `getmembers()` scan and EOF `fileobj.read()` use the same lock |
| Member operation raises/closes | Translation/logging/lifecycle/callback work runs without the TAR handle lock held |
| GNU sparse member opened | Stream yields the same logical bytes as stdlib sparse handling |
| `streaming=True` TAR | Forward-only contract unchanged; no concurrent random-open behavior |
| Contention on shared handle | Correctness guaranteed; no correctness speed threshold |
