# Format TAR — delta (tar-concurrent-open)

## ADDED Requirements

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
