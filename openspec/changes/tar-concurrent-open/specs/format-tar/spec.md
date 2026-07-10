# Format TAR — delta (tar-concurrent-open)

## ADDED Requirements

### Requirement: Random-access TAR concurrent member open via SharedSource

The system SHALL support interleaved concurrent member data streams from one
**random-access** TAR reader (`streaming=False`) when the uncompressed tar byte stream is
seekable **and the caller has opted in to multiple open streams** (`allow_multiple_open_streams`,
per `concurrent-open-opt-in`). Without the opt-in, a second overlapping open raises uniformly
(the default single-live-stream gate) — TAR is not special-cased here. The reader MUST wrap the
uncompressed stream in a shared-source primitive and serve file-member payloads through per-open
byte-range views at `TarInfo.offset_data` (with the member size), rather than relying on two
concurrent `tarfile.extractfile` streams over one shared file position.

The implementation MUST apply a **forward-cursor** view policy: reuse one view for opens
that seek at or past the cursor when that view is not busy; mint another view when an
earlier offset is needed while a view is still in use. It MUST NOT open a fresh view on
every member unconditionally when forward reuse is safe (sequential extract MUST NOT
regress to per-member view churn without cause).

**Compressed TAR.** Concurrent open does not change the access-cost model: a compressed
TAR remains a single compression stream (`SOLID`). Seekability of the post-decode stream
is what enables correct interleaved opens; it does not imply cheap random access into the
compressed bytes.

**Out of scope.** Streaming TAR (`streaming=True` / `r|`) remains single-pass.
Random-access TAR whose uncompressed stream is not seekable is not required to support
interleaved concurrent opens until a seekable layer exists.

#### Scenario: interleaved opens on plain TAR-RA

- **WHEN** two file members of a plain random-access TAR are opened and read interleaved
- **THEN** each stream yields that member's exact bytes in order

#### Scenario: interleaved opens on compressed TAR-RA with a seekable uncompressed stream

- **WHEN** two file members of a compressed random-access TAR (e.g. `.tar.gz`) whose
  uncompressed stream is seekable are opened and read interleaved
- **THEN** each stream yields that member's exact bytes in order

#### Scenario: sequential extract reuses a forward cursor

- **WHEN** members of a random-access TAR are opened and fully read in archive order
- **THEN** the reader reuses a forward-cursor view across those opens rather than minting
  an independent view for every member when reuse is safe

#### Scenario: streaming TAR is unchanged

- **WHEN** a TAR archive is opened with `streaming=True`
- **THEN** the concurrent-open SharedSource data path is not required; the reader remains
  forward-only
