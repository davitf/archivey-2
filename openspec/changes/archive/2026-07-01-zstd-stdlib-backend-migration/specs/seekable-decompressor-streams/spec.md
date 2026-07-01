# Seekable Decompressor Streams — delta (zstd stdlib backend migration)

## MODIFIED Requirements

### Requirement: Index-less codecs warn on a rewinding seek

The system SHALL service a backward seek on an index-less codec (gzip/bzip2 without an
accelerator, and brotli/lz4/zstd/zlib) by re-decompressing from the start — O(n) per rewind —
and SHALL log a one-time rewind warning via the `archivey` streams logger rather than rewinding
silently. With the stdlib zstd backend (`compression.zstd` / `backports.zstd`), zstd rewinds
**in place** like the other index-less codecs, so the previous reopen-from-source special case
is removed; the cost and the warning are unchanged. Where an accelerator backend exists
(gzip, bzip2) the warning names the `[seekable]` extra; for brotli/lz4/zstd/zlib it states that
the codec re-decompresses from the start. Forward and no-op seeks SHALL NOT warn, and codecs
that carry their own index (xz, lzip, unix-compress) SHALL NOT warn.

#### Scenario: rewinding an index-less codec warns

- **WHEN** a brotli, lz4, zstd, or zlib stream is read and then seeked backward to an earlier offset
- **THEN** the data is delivered correctly **AND** a warning is logged that the codec re-decompresses from the start (no accelerator is named, because none exists for these codecs)

#### Scenario: zstd rewinds in place via the stdlib backend

- **WHEN** a zstd stream is read forward and then seeked backward
- **THEN** the stdlib `ZstdFile` services the rewind by re-decompressing from the start (no reopen-from-source special case) and logs the index-less rewind warning once

#### Scenario: a forward-only seek does not warn

- **WHEN** an index-less codec stream is seeked only forward (or to its current position)
- **THEN** no rewind warning is logged
