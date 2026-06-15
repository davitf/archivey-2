# Single-File Compressor Format Behavior (GZ, BZ2, XZ, ZST)

## Purpose

Single-file compressors (GZ, BZ2, XZ, ZST) are presented as a one-member pseudo-archive through the unified `ArchiveReader` / `ArchiveWriter` interface. The compressed stream is treated as an archive containing exactly one file, with the member name inferred from the source filename. This allows single-file compressed streams to participate in the same iteration, extraction, and conversion workflows as multi-member archives.

## Requirements

### Requirement: Present a single-file compressor as a one-member archive

The system SHALL present any GZ, BZ2, XZ, or ZST source as an archive containing exactly one `Member` of type `MemberType.FILE`. No directory members are synthesized. The member's name is inferred by stripping the compression extension from the source filename (e.g., `data.txt.gz` → `data.txt`). If no filename is available (e.g., the source is an anonymous stream), the member name defaults to `"data"`.

#### Scenario: Member name inferred from filename

- **WHEN** a single-file compressor archive is opened from a path such as `data.txt.gz`
- **THEN** the single member's `name` is `"data.txt"` (the compression extension `.gz` is stripped)

#### Scenario: Member name defaults when no filename is available

- **WHEN** a single-file compressor archive is opened from a non-seekable stream with no associated filename
- **THEN** the single member's `name` is `"data"`

#### Scenario: Exactly one member is present

- **WHEN** any GZ, BZ2, XZ, or ZST source is opened
- **THEN** iterating the reader yields exactly one `Member`

### Requirement: Surface the gzip stored filename in `raw_filename`

The gzip format optionally records the original filename in its header (the `FNAME`
field). When present, the system SHALL expose it in the member's `raw_filename`. By
default the member `name` is still inferred from the *source* filename (stripping the
`.gz` extension); the embedded name is not automatically trusted as the logical name,
since it may disagree with the container filename. A configuration option MAY direct
the reader to prefer the gzip-stored name for `name`. The other single-file
compressors (BZ2, XZ, ZST) carry no embedded filename, so their `raw_filename` is
`None`.

#### Scenario: gzip with a stored filename

- **WHEN** a `.gz` stream whose header carries `FNAME = "report.csv"` is opened from a path like `archive.gz`
- **THEN** the member's `raw_filename` is `"report.csv"`, while `name` remains `"archive"` (derived from the source filename) by default

#### Scenario: gzip without a stored filename

- **WHEN** a `.gz` stream has no `FNAME` header field
- **THEN** the member's `raw_filename` is `None` and `name` is derived from the source filename

### Requirement: Report single-file compressor format properties

The system SHALL expose the following cost and capability properties for every opened single-file compressor archive:

| Property | Value |
|----------|-------|
| Listing cost | O(1) — one member always |
| Access cost | SOLID by default; reduced when a seek-capable backend is active (see note) |
| Supports write | Yes |
| Requires seek | No |

The default access cost is SOLID — plain decompression must run from the start to
reach a given offset. However, several formats and backends support **limited or
full random access** within the single stream, and the access cost SHALL reflect the
backend actually in use: e.g. xz with its block index, bzip2 with a block index
(`indexed_bzip2`), gzip via `rapidgzip`, and seekable-zstd. These are provided by the
`seekable-decompressor-streams` capability; when such a backend is active the reader
MAY serve random reads without re-decompressing from the start, and reports the
corresponding (non-SOLID) access cost and `seekable` flag.

#### Scenario: CostReceipt on open with the default backend

- **WHEN** a GZ, BZ2, XZ, or ZST archive is opened with the default (non-seeking) backend
- **THEN** `cost.listing_cost` is `ListingCost.O1` and `cost.access_cost` is `AccessCost.SOLID`

#### Scenario: seek-capable backend lowers the access cost

- **WHEN** the archive is opened with a seek-capable backend (e.g. `indexed_bzip2` for `.bz2`, or an xz stream with a block index)
- **THEN** the reported `cost.access_cost` reflects the random-access capability rather than `AccessCost.SOLID`, per `seekable-decompressor-streams`

### Requirement: Report member size with format-specific caveats

The system SHALL populate `member.size` (uncompressed size) according to format-specific limitations:

- **GZ:** `member.size` is `None`. The GZ format stores the uncompressed size modulo 2³², making it unreliable for files larger than 4 GiB. The field is never reported to avoid silently returning a wrong value.
- **BZ2:** `member.size` is `None` until the stream has been fully decompressed. The BZ2 format does not store the uncompressed size in its header; the size becomes known only after full decompression.
- **XZ** and **ZST:** `member.size` may be available from the stream header if the encoder wrote it; otherwise `None`.

#### Scenario: GZ member size is always None

- **WHEN** a `.gz` archive is opened
- **THEN** `member.size` is `None` for the single member

#### Scenario: BZ2 member size before full decompression

- **WHEN** a `.bz2` archive is opened and the member has not yet been fully decompressed
- **THEN** `member.size` is `None`

#### Scenario: BZ2 member size after full decompression

- **WHEN** a `.bz2` archive is opened and the member stream has been fully read to EOF
- **THEN** `member.size` may be updated to reflect the actual uncompressed byte count
