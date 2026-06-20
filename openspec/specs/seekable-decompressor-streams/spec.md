# Seekable Decompressor Streams

## Purpose

Archivey (DEV) provides a subsystem that gives random access inside single-file compressed streams — formats that would otherwise require full decompression from the start. This is achieved by exploiting format-native index structures and optional accelerator backends, enabling use cases such as cheaply reading the last member of a multi-gigabyte `.tar.xz`.

## Requirements

### Requirement: Seekable random access via format-native indexes

The system SHALL support seekable random access within XZ and lzip compressed streams by reading the index structures embedded in those formats. For XZ, this is done by parsing the XZ stream footer and block index, which records the uncompressed offset of each block without requiring full decompression. For lzip, this is done by scanning the lzip trailer at the end of the stream. These index-based approaches make it possible to seek to an arbitrary uncompressed offset by decompressing only the block(s) that contain it.

#### Scenario: seeking within an XZ stream using the block index

- **WHEN** a seekable source containing an XZ-compressed stream is opened
- **THEN** the system reads the XZ stream footer and block index to construct a mapping from uncompressed offsets to compressed block positions
- **AND** a subsequent seek to an arbitrary uncompressed offset decompresses only the block(s) containing that offset, not the entire stream from the start

#### Scenario: seeking within a lzip stream using the trailer scan

- **WHEN** a seekable source containing a lzip-compressed stream is opened
- **THEN** the system scans the lzip trailer to locate block boundaries
- **AND** a subsequent seek to an arbitrary uncompressed offset decompresses only the required block(s)

### Requirement: Optional accelerator backends for gzip and bzip2 random access

The system SHALL support optional accelerator backends for formats that have no native block index. For gzip, the `rapidgzip` library may be used as a backend to enable random access. For bzip2, the `indexed_bzip2` library may be used. These backends are opt-in (controlled by `use_rapidgzip` and `use_indexed_bzip2` configuration flags, which in v2 will be tri-state `AUTO`/`ON`/`OFF` resolved against the caller's access mode — the `streaming` flag). When neither accelerator is available or enabled, gzip and bzip2 streams stay backed by the stdlib decoders, which still support seeking but service it by re-decompressing from the start (O(n) per rewind). The slow path is permitted — not every format can offer fast random access, and a slow seek beats failing — but it MUST NOT be silent: a seek that rewinds the stream SHALL log a warning naming the `[seekable]` accelerator.

#### Scenario: gzip random access with rapidgzip enabled

- **WHEN** `use_rapidgzip` is enabled and the `rapidgzip` package is installed
- **THEN** a gzip-compressed stream supports seeking to arbitrary uncompressed offsets without decompressing from the start

#### Scenario: bzip2 random access with indexed_bzip2 enabled

- **WHEN** `use_indexed_bzip2` is enabled and the `indexed_bzip2` package is installed
- **THEN** a bzip2-compressed stream supports seeking to arbitrary uncompressed offsets without decompressing from the start

#### Scenario: accelerator backend absent

- **WHEN** neither `rapidgzip` nor `indexed_bzip2` is installed, or the corresponding flag is `OFF`
- **THEN** gzip and bzip2 streams stay backed by the stdlib decoders, which service a seek only by re-decompressing from the start (O(n) per rewind)
- **AND** a seek that rewinds the stream logs a warning naming the `[seekable]` accelerator, rather than degrading silently or failing

### Requirement: Index-less codecs warn on a rewinding seek

A codec with no random-access index services a backward seek by re-decompressing the
stream from the start — O(n) per rewind. This applies to gzip and bzip2 without an
accelerator (above) and, with no accelerator available at all, to **brotli, lz4, zstd, and
zlib**. zstd's reader cannot seek backward in place, so a backward seek reopens the source
from the start and re-decompresses forward — the same O(n) cost, surfaced the same way,
rather than raising. The slow path is permitted (a slow seek beats failing, and not every
format can offer fast random access), but it SHALL NOT be silent: the first seek that
rewinds such a stream SHALL log a warning via the `archivey` streams logger. Where an
accelerator backend exists (gzip, bzip2) the warning names the `[seekable]` extra; for
brotli/lz4/zstd/zlib, which have no accelerator, it states that the codec re-decompresses
from the start. Forward seeks and no-op seeks do not warn.

Codecs that carry their own index (xz, lzip, unix-compress) seek efficiently and SHALL NOT
warn.

#### Scenario: rewinding an index-less codec warns

- **WHEN** a brotli, lz4, zstd, or zlib stream is read and then seeked backward to an earlier offset
- **THEN** the data is delivered correctly **AND** a warning is logged that the codec re-decompresses from the start (no accelerator is named, because none exists for these codecs)

#### Scenario: a forward-only seek does not warn

- **WHEN** an index-less codec stream is seeked only forward (or to its current position)
- **THEN** no rewind warning is logged
