# seekable-decompressor-streams — runtime seek diagnostics

## MODIFIED Requirements

### Requirement: Index-less codecs warn on a rewinding seek

When an index-less codec first services a backward seek by re-decompressing from the
start, the system SHALL emit `STREAM_REWIND_REDECOMPRESSES` with codec, before/after
offsets, and accelerator name (or `None`) in typed context. It SHALL be emitted at most
once per stream, matching the existing warning's transition semantics; exact counts
therefore report affected streams, not every seek call.

The event SHALL live on the stream operation and cumulative owning-reader aggregate, never
on `CostReceipt` or `ArchiveInfo`. Diagnostic policy controls logging, callback delivery,
and escalation. Forward/no-op seeks SHALL emit nothing.

This applies to gzip/bzip2 when their accelerator is unavailable or disabled and to
brotli, lz4, zstd, and zlib, which have no random-access index. Gzip/bzip2 context names
the `[seekable]` accelerator; the other codecs record no accelerator. XZ, lzip, and
unix-compress use their own indexes and SHALL not emit this event for indexed seeks.

#### Scenario: repeated rewinds emit once for the stream

- **WHEN** one index-less stream performs 1,000 backward seeks
- **THEN** it emits one `STREAM_REWIND_REDECOMPRESSES` occurrence, later rewinds emit no duplicate, and no open-time metadata object changes

#### Scenario: raised rewind halts at the seek

- **WHEN** `STREAM_REWIND_REDECOMPRESSES` resolves to `RAISE`
- **THEN** the backward seek's diagnostic is delivered and `DiagnosticRaisedError` is raised from that seek operation

#### Scenario: forward seek has no rewind event

- **WHEN** an index-less stream seeks only forward or to its current position
- **THEN** no `STREAM_REWIND_REDECOMPRESSES` occurrence is emitted

## ADDED Requirements

### Requirement: Optional seek-index degradation is diagnostic data

When an XZ/lzip backward index or trailer scan fails in a way for which the stream can
safely fall back to sequential decompression, the system SHALL emit
`SEEK_INDEX_DEGRADED` with codec, scan kind, and public failure type in typed context.
The occurrence SHALL be aggregate-only on the stream/reader operation.

If policy escalates the code, the stream SHALL halt with `DiagnosticRaisedError` instead
of taking the fallback. Genuine corruption that already makes decoding unsafe remains its
typed read exception rather than a diagnostic.

#### Scenario: recoverable XZ index failure falls back under default policy

- **WHEN** an XZ backward index scan fails but sequential decompression remains valid
- **THEN** `SEEK_INDEX_DEGRADED` is collected/logged and the stream uses sequential fallback

#### Scenario: unsafe corruption remains an error

- **WHEN** the same corruption prevents correct sequential decoding
- **THEN** the appropriate `CorruptionError`/`TruncatedError` is raised rather than converting the failure to a recoverable diagnostic
