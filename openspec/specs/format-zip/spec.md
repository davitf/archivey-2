# ZIP Format Behavior

## Purpose

The ZIP backend presents ZIP archives through the unified `ArchiveReader` / `ArchiveWriter` interface using Python's stdlib `zipfile` module. It reads the central directory on open for O(1) member listing, supports direct random access to any member, and can write archives in streaming mode using data descriptors.

## Requirements

### Requirement: Report ZIP format properties

The system SHALL expose the following cost and capability properties for every opened ZIP archive:

| Property | Value |
|----------|-------|
| Backend dependency | `zipfile` (stdlib) |
| Listing cost | O(1) — central directory is read first |
| Access cost | DIRECT — independent local file offsets |
| Supports write | Yes |
| Requires seek | Yes for read (central dir at EOF); No for streaming write |

#### Scenario: CostReceipt on open

- **WHEN** a ZIP archive is opened with `archivey.open_archive()`
- **THEN** the returned reader's `cost` property reports `ListingCost.O1`, `AccessCost.DIRECT`, and `StreamCapability.SEEKABLE`

#### Scenario: Central directory lookup is O(1)

- **WHEN** `reader["some/member.txt"]` is called on a ZIP reader
- **THEN** the lookup is satisfied via the in-memory `NameToInfo` dict with no additional I/O

### Requirement: Map ZIP member metadata to the unified Member model

The system SHALL map each `ZipInfo` entry to a `Member` dataclass using the following field rules:

- `mode`: parsed from `external_attr >> 16`. If `external_attr == 0` and `create_system != 3` (Unix), `mode` is set to `None`.
- `modified`: from the `date_time` tuple, constructed as a naive `datetime` (no TZ; DOS format has 2-second granularity). If the ZIP64 extra field contains an NT timestamp, use that as a timezone-aware UTC `datetime` instead.
- `type`: inferred from `mode` if Unix, otherwise from `is_dir()` and symlink detection via extra field `0x000A` (NTFS) or `0x7875` (Unix UID/GID).
- `compression`: map `compress_type` integer to `CompressionMethod`.
- `is_encrypted`: set to `True` when `flag_bits & 0x1` is non-zero.

#### Scenario: Unix mode from external_attr

- **WHEN** a ZIP entry has `create_system == 3` (Unix) and a non-zero `external_attr`
- **THEN** `member.mode` is set to `external_attr >> 16` (low 12 bits: permission bits)

#### Scenario: Non-Unix or missing mode

- **WHEN** a ZIP entry has `external_attr == 0` or `create_system != 3`
- **THEN** `member.mode` is set to `None`

#### Scenario: NT timestamp takes precedence over DOS date_time

- **WHEN** a ZIP entry carries a ZIP64 extra field containing an NT timestamp
- **THEN** `member.modified` is a timezone-aware UTC `datetime` derived from that NT timestamp, overriding the value from `date_time`

#### Scenario: Encrypted entry detection

- **WHEN** a ZIP entry has `flag_bits & 0x1` set
- **THEN** `member.is_encrypted` is `True`

### Requirement: Handle non-seekable ZIP streams by spooling

The system SHALL buffer a non-seekable ZIP source into a `tempfile.SpooledTemporaryFile` before opening, because the ZIP central directory resides at the end of the file and cannot be read without seeking.

- The spool threshold is configurable as `spool_max_size` (default: 50 MiB).
- If the ZIP stream data exceeds `spool_max_size` before the central directory is reached, the system SHALL raise `ReadError` with a message advising the caller to save the archive to disk first.
- Opening a non-seekable ZIP stream with `Intent.RANDOM` is not supported; the backend rejects this combination.

#### Scenario: Small non-seekable ZIP is spooled successfully

- **WHEN** a ZIP stream is opened from a non-seekable source (e.g., a network pipe) with `Intent.SEQUENTIAL` or `Intent.AUTO`
- **AND** the total archive size is within `spool_max_size`
- **THEN** the backend transparently buffers the stream and opens the archive normally

#### Scenario: Oversized non-seekable ZIP raises ReadError

- **WHEN** a ZIP stream is opened from a non-seekable source
- **AND** the archive size exceeds `spool_max_size`
- **THEN** the system raises `ReadError` with a hint to save the archive to disk first

#### Scenario: RANDOM intent rejected on non-seekable source

- **WHEN** a ZIP stream is opened from a non-seekable source with `Intent.RANDOM`
- **THEN** the system raises an error indicating random access is unavailable for non-seekable ZIP streams

### Requirement: Support streaming ZIP write via data descriptor

The system SHALL support writing ZIP archives to non-seekable destinations using the data descriptor mechanism.

When writing, the backend sets `flag_bits |= 0x8` (data descriptor flag), which allows the CRC-32 and compressed/uncompressed sizes to be written after the file data rather than before. File size is therefore not required in advance from the caller.

#### Scenario: Streaming write without pre-known size

- **WHEN** `writer.add_stream(stream, name=...)` is called without a `size` argument
- **THEN** the ZIP backend writes the local file header with placeholder CRC and sizes, streams the data, and appends a data descriptor record with the actual CRC-32 and sizes
- **AND** the resulting ZIP file is valid and readable by standard ZIP tools
