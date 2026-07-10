# Format TAR — delta (tar-concurrent-open)

## ADDED Requirements

### Requirement: Random-access TAR concurrent member open via locked extractfile streams

The system SHALL support interleaved concurrent member data streams from one
**random-access** TAR reader (`streaming=False`) when the caller has opted in to multiple
open streams (`allow_multiple_open_streams`, per `concurrent-open-opt-in`). Without the
opt-in, a second overlapping open raises uniformly — TAR is not special-cased. The reader
MUST continue to obtain file member payloads through `tarfile.extractfile` (preserving
sparse and other stdlib behavior) and MUST wrap each returned stream so that every
data-path read holds a **per-archive lock** for the duration of the library `read`,
serializing seek-before-read on the shared tar fileobj.

**Compressed TAR.** Concurrent open does not change the access-cost model: a compressed
TAR remains a single compression stream (`SOLID`).

**Out of scope.** Streaming TAR (`streaming=True` / `r|`) remains single-pass. Replacing
tarfile with a native reader or serving members via shared-source views at `offset_data`
is not required by this capability.

#### Scenario: interleaved opens on plain TAR-RA

- **WHEN** `allow_multiple_open_streams` is enabled and two file members of a plain
  random-access TAR are opened and read interleaved
- **THEN** each stream yields that member's exact bytes in order

#### Scenario: interleaved opens on compressed TAR-RA

- **WHEN** `allow_multiple_open_streams` is enabled and two file members of a compressed
  random-access TAR (e.g. `.tar.gz`) are opened and read interleaved
- **THEN** each stream yields that member's exact bytes in order

#### Scenario: sparse members still expand correctly

- **WHEN** a GNU sparse file member is opened from a random-access TAR
- **THEN** the stream yields the same logical bytes as before this change (stdlib sparse
  handling is preserved)

#### Scenario: streaming TAR is unchanged

- **WHEN** a TAR archive is opened with `streaming=True`
- **THEN** the locked concurrent-open wrap is not required; the reader remains forward-only
