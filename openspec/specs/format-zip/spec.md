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

- **WHEN** `reader.get("some/member.txt")` is called on a ZIP reader
- **THEN** the lookup is satisfied via the in-memory `NameToInfo` dict with no additional I/O

### Requirement: Map ZIP member metadata to the unified ArchiveMember model

The system SHALL map each `ZipInfo` entry to a `ArchiveMember` dataclass using the following field rules:

- `mode`: parsed from `external_attr >> 16`. If `external_attr == 0` and `create_system != 3` (Unix), `mode` is set to `None`.
- `modified`/`accessed`/`created`: layered by precedence, each layer overriding only the
  times it actually carries. Base: the DOS `date_time` tuple as a naive `datetime` (no TZ;
  local wall-clock, 2-second granularity; `None` for the year-1980 "no timestamp"
  sentinel). Above it: the NTFS extra field (`0x000A`) — three 64-bit FILETIMEs
  (modification/access/creation, 100 ns UTC ticks since 1601, zero = "not set"; written
  by Windows tools such as 7-Zip) — as timezone-aware UTC `datetime`s. Highest: the
  Extended Timestamp extra field (`0x5455`) — signed 32-bit Unix times, its flags byte
  signaling which of modification/access/creation are present — as timezone-aware UTC
  `datetime`s.
- `type`: inferred from `mode` if Unix, otherwise from `is_dir()` and symlink detection via extra field `0x000A` (NTFS) or `0x7875` (Unix UID/GID).
- `compression`: map `compress_type` integer to `CompressionMethod`.
- `is_encrypted`: set to `True` when `flag_bits & 0x1` is non-zero.

> **Phase 3 → 7 gap (member decode via stdlib zipfile).** Until the
> `compressed-streams` codec layer is wired into ZIP member reads (Phase 7, alongside
> the native 7z reader's container codecs), member *data* decompression goes through
> stdlib `zipfile`, which cannot decode deflate64/PPMd (or zstd before Python 3.14)
> even when the corresponding codec packages are installed — reading such a member
> raises `UnsupportedFeatureError`. `format_availability(ZIP)`'s FULL/PARTIAL result
> (see `backend-registry`) describes the intended post-Phase-7 composition over those
> codecs, so until then it can report FULL while these rare member codecs still fail
> at read time. Listing is unaffected.

#### Scenario: Unix mode from external_attr

- **WHEN** a ZIP entry has `create_system == 3` (Unix) and a non-zero `external_attr`
- **THEN** `member.mode` is set to `external_attr >> 16` (low 12 bits: permission bits)

#### Scenario: Non-Unix or missing mode

- **WHEN** a ZIP entry has `external_attr == 0` or `create_system != 3`
- **THEN** `member.mode` is set to `None`

#### Scenario: Extended Timestamp takes precedence over DOS date_time

- **WHEN** a ZIP entry carries an Extended Timestamp extra field (`0x5455`) with a modification time
- **THEN** `member.modified` is a timezone-aware UTC `datetime` derived from that Unix time, overriding the value from `date_time`

#### Scenario: NTFS timestamps used when no Extended Timestamp is present

- **WHEN** a ZIP entry carries an NTFS extra field (`0x000A`) with non-zero FILETIMEs and no `0x5455` field
- **THEN** `member.modified`/`accessed`/`created` are timezone-aware UTC `datetime`s derived from those FILETIMEs, overriding the value from `date_time`

#### Scenario: Encrypted entry detection

- **WHEN** a ZIP entry has `flag_bits & 0x1` set
- **THEN** `member.is_encrypted` is `True`

### Requirement: Handle non-seekable ZIP streams

The ZIP central directory resides at the **end** of the file, so a ZIP cannot be read from a non-seekable source (a pipe/socket) without first buffering it to seekable storage. Per the access-mode contract (`access-mode-and-cost`), the system SHALL raise `StreamNotSeekableError` at open time for a non-seekable ZIP source, advising the caller to buffer the source (save to disk or a `BytesIO`) and reopen, rather than buffering implicitly.

> **Reconcile when the ZIP backend lands (Phase 3).** The earlier design auto-spooled a non-seekable ZIP into a `tempfile.SpooledTemporaryFile` transparently (threshold `spool_max_size`, default 50 MiB; oversized → `ReadError`). That convenience conflicts with the decided rule that `streaming=False` **fails fast** on a source it cannot random-access and the library does **not** implicitly buffer. If transparent spooling is wanted back, it must return as an **explicit opt-in** (e.g. a `spool_max_size` argument), not the default. Finalize this when the backend is implemented.

#### Scenario: non-seekable ZIP fails fast

- **WHEN** a ZIP stream is opened from a non-seekable source (e.g. a network pipe) with the default `streaming=False`
- **THEN** `StreamNotSeekableError` is raised at open time, advising the caller to buffer the source and reopen

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
