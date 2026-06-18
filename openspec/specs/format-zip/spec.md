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
- **THEN** the returned reader's `cost` property reports `ListingCost.INDEXED`, `AccessCost.DIRECT`, and `StreamCapability.SEEKABLE`

#### Scenario: Central directory lookup is O(1)

- **WHEN** `reader["some/member.txt"]` is called on a ZIP reader
- **THEN** the lookup is satisfied via the in-memory `NameToInfo` dict with no additional I/O

### Requirement: Map ZIP member metadata to the unified ArchiveMember model

The system SHALL map each `ZipInfo` entry to a `ArchiveMember` dataclass using the following field rules:

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

- **WHEN** a ZIP stream is opened from a non-seekable source (e.g., a network pipe) with `Intent.SEQUENTIAL` or `Intent.DEFAULT`
- **AND** the total archive size is within `spool_max_size`
- **THEN** the backend transparently buffers the stream and opens the archive normally

#### Scenario: Oversized non-seekable ZIP raises ReadError

- **WHEN** a ZIP stream is opened from a non-seekable source
- **AND** the archive size exceeds `spool_max_size`
- **THEN** the system raises `ReadError` with a hint to save the archive to disk first

#### Scenario: RANDOM intent rejected on non-seekable source

- **WHEN** a ZIP stream is opened from a non-seekable source with `Intent.RANDOM`
- **THEN** the system raises an error indicating random access is unavailable for non-seekable ZIP streams

### Requirement: Reject multi-volume (split/spanned) ZIP archives with a clear error

Unlike multi-volume 7z and RAR (which Archivey joins — see `format-7z` and
`format-rar`), the stdlib `zipfile` backend cannot read a multi-volume ZIP. A ZIP
**split** set (`name.z01`, `name.z02`, …, final `name.zip`) or a **spanned** set
(written across removable media) records each entry's location as a
*(disk-number, offset-within-disk)* pair; `zipfile` rejects the ZIP64 multi-disk
locator outright, and naive concatenation of the segments is unreliable (non-zero disk
fields in the end-of-central-directory, a possible leading spanning marker, and
non-absolute offsets). The system SHALL detect this case and raise
`UnsupportedFeatureError` rather than mis-reading the archive or surfacing a cryptic
stdlib `BadZipFile`.

- Detection MAY use: a non-zero "number of this disk" / "disk where the central
  directory starts" field in the (ZIP64) end-of-central-directory record, a `disks > 1`
  ZIP64 EOCD locator, or being pointed at a `.z01`/`.zNN` segment.
- The error message SHOULD advise the caller to rejoin the volumes first
  (e.g. `zip -s 0 split.zip --out whole.zip`).
- Proper multi-volume ZIP support is deferred to a future **native ZIP reader**
  (see `IDEAS.md`), which can resolve *(disk, offset)* addressing across a
  concatenation of the segments.

#### Scenario: opening a split ZIP set is rejected

- **WHEN** `open_archive()` is given a multi-volume ZIP (a `.z01`…`.zip` split set, or any segment of one)
- **THEN** `UnsupportedFeatureError` is raised, advising the caller to rejoin the volumes first

#### Scenario: a ZIP declaring multiple disks is rejected cleanly

- **WHEN** a ZIP whose end-of-central-directory declares a non-zero disk number (or a ZIP64 locator with `disks > 1`) is opened
- **THEN** `UnsupportedFeatureError` is raised rather than a stdlib `BadZipFile`

### Requirement: Support streaming ZIP write via data descriptor

The system SHALL support writing ZIP archives to non-seekable destinations using the data descriptor mechanism.

When writing, the backend sets `flag_bits |= 0x8` (data descriptor flag), which allows the CRC-32 and compressed/uncompressed sizes to be written after the file data rather than before. File size is therefore not required in advance from the caller.

#### Scenario: Streaming write without pre-known size

- **WHEN** `writer.add_stream(stream, name=...)` is called without a `size` argument
- **THEN** the ZIP backend writes the local file header with placeholder CRC and sizes, streams the data, and appends a data descriptor record with the actual CRC-32 and sizes
- **AND** the resulting ZIP file is valid and readable by standard ZIP tools
