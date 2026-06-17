# TAR Format Behavior

## Purpose

The TAR backend presents all TAR variants (plain `.tar`, and compressed `.tar.gz`, `.tar.bz2`, `.tar.xz`, `.tar.zst`) through the unified `ArchiveReader` / `ArchiveWriter` interface using Python's stdlib `tarfile` module. It reads sequentially with no central directory, supports streaming writes, and handles TAR-specific semantics including PAX extended headers, hardlink two-pass extraction, and truncation detection.

## Requirements

### Requirement: Report TAR format properties

The system SHALL expose the following cost and capability properties for every opened TAR archive:

| Property | Value |
|----------|-------|
| Backend dependency | `tarfile` (stdlib) |
| Listing cost | No central directory: `REQUIRES_DECOMPRESSION` for compressed tars (must inflate to reach headers), `REQUIRES_SCANNING` for plain `.tar` (walk 512-byte headers, no decompress) |
| Access cost | SOLID for `.tar.gz`, `.tar.bz2`, `.tar.xz`, `.tar.zst`; DIRECT for plain `.tar` |
| Supports write | Yes |
| Requires seek | No (streaming mode) |

#### Scenario: CostReceipt for compressed TAR

- **WHEN** an archive with format `TAR_GZ`, `TAR_BZ2`, `TAR_XZ`, or `TAR_ZST` is opened
- **THEN** `cost.access_cost` is `AccessCost.SOLID` and `cost.listing_cost` is `ListingCost.REQUIRES_DECOMPRESSION`

#### Scenario: CostReceipt for plain TAR

- **WHEN** an archive with format `TAR` (plain, uncompressed) is opened
- **THEN** `cost.access_cost` is `AccessCost.DIRECT` and `cost.listing_cost` is `ListingCost.REQUIRES_SCANNING`

### Requirement: Map TAR member metadata to the unified ArchiveMember model

The system SHALL map each `TarInfo` entry to a `ArchiveMember` dataclass using the following field rules:

- `mode`: from `TarInfo.mode` (lower 12 bits).
- `modified`: from `TarInfo.mtime` (Unix timestamp), interpreted as UTC and returned as a timezone-aware `datetime`.
- PAX extended headers (`pax_headers`) override `mtime` with full precision and optional timezone information when present.
- `uname`, `gname`, `uid`, `gid`: taken directly from the corresponding `TarInfo` fields.
- `type`: mapped from the TAR type byte (`REGTYPE`, `DIRTYPE`, `SYMTYPE`, `LNKTYPE`, etc.) to the corresponding `MemberType` value.

#### Scenario: PAX header overrides mtime

- **WHEN** a TAR member carries PAX extended headers that include an `mtime` field
- **THEN** `member.modified` is derived from the PAX `mtime` value (which may carry sub-second precision and timezone information), overriding the value from `TarInfo.mtime`

#### Scenario: Standard mtime mapping

- **WHEN** a TAR member carries no PAX `mtime` override
- **THEN** `member.modified` is a timezone-aware UTC `datetime` constructed from `TarInfo.mtime` (Unix timestamp)

#### Scenario: Hardlink type mapping

- **WHEN** a TAR entry has type byte `LNKTYPE`
- **THEN** `member.type` is `MemberType.HARDLINK` and `member.link_target` is set to the `linkname` field

### Requirement: Handle TAR hardlinks via two-pass extraction with cross-device fallback

The system SHALL support hardlink extraction from TAR archives. The `linkname` field holds the target path.

During extraction:

1. If the hardlink target has already been extracted, the system creates an actual filesystem hardlink via `os.link`.
2. If the hardlink target has not yet been extracted (link precedes source in archive order), the system defers creation to a post-pass once all members are written.
3. If hardlink creation fails due to a cross-device link error, the system falls back to copying the source file.

#### Scenario: Hardlink target already extracted

- **WHEN** a `HARDLINK` member is encountered during extraction
- **AND** the link target has already been written to disk
- **THEN** the system creates a filesystem hardlink from the target path to the new path via `os.link`

#### Scenario: Hardlink target not yet extracted

- **WHEN** a `HARDLINK` member is encountered during extraction
- **AND** the link target has not yet been written to disk
- **THEN** the system defers link creation and resolves it in a post-extraction pass once the target member is written

#### Scenario: Cross-device hardlink falls back to copy

- **WHEN** `os.link` fails with a cross-device error
- **THEN** the system copies the source file to the link destination instead

### Requirement: Detect truncated TAR archives

The system SHALL verify archive integrity at the end of iteration by checking for valid end-of-archive markers.

After iterating all members, the system verifies that the final 512-byte block(s) are null-filled end-of-archive markers. If the markers are absent:

- By default (`strict_eof=False`): emit a `logging.WARNING` via the `archivey.backends.*` logger.
- When `strict_eof=True`: raise `TruncatedError`.

#### Scenario: Valid TAR end-of-archive markers present

- **WHEN** all TAR members have been iterated
- **AND** the archive ends with null-filled 512-byte end-of-archive block(s)
- **THEN** no warning or error is emitted

#### Scenario: Missing end-of-archive markers, default mode

- **WHEN** all TAR members have been iterated
- **AND** the archive does not end with valid null-filled end-of-archive block(s)
- **AND** `strict_eof` is `False` (the default)
- **THEN** the system emits a `logging.WARNING` indicating the archive may be truncated

#### Scenario: Missing end-of-archive markers, strict mode

- **WHEN** all TAR members have been iterated
- **AND** the archive does not end with valid null-filled end-of-archive block(s)
- **AND** `strict_eof` is `True`
- **THEN** the system raises `TruncatedError`

### Requirement: Detect TAR compression variant from magic bytes

The system SHALL detect the compression variant of a TAR archive from the magic bytes of its first bytes and map the result to the appropriate `tarfile` mode string.

Detected compression variants and their `tarfile` mode strings:

| Compression | `tarfile` mode |
|-------------|----------------|
| gzip | `r:gz` |
| bzip2 | `r:bz2` |
| xz / lzma | `r:xz` |
| auto-detect | `r:*` |
| none (plain) | `r:` |

#### Scenario: Compressed TAR opened in correct mode

- **WHEN** a `.tar.gz` file is opened
- **THEN** the `tarfile` backend is invoked with mode `r:gz`

#### Scenario: Plain TAR opened without decompression

- **WHEN** a plain `.tar` file is opened
- **THEN** the `tarfile` backend is invoked with mode `r:` (no decompression wrapper)
