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

### Requirement: Report single-file compressor format properties

The system SHALL expose the following cost and capability properties for every opened single-file compressor archive:

| Property | Value |
|----------|-------|
| Listing cost | O(1) — one member always |
| Access cost | SOLID (must decompress from start to reach any byte offset) |
| Supports write | Yes |
| Requires seek | No |

#### Scenario: CostReceipt on open

- **WHEN** a GZ, BZ2, XZ, or ZST archive is opened
- **THEN** `cost.listing_cost` is `ListingCost.O1` and `cost.access_cost` is `AccessCost.SOLID`

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
