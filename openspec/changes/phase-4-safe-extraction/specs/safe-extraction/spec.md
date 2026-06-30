# Safe Extraction — delta (phase-4-safe-extraction)

## ADDED Requirements

### Requirement: Archive-wide decompression ratio for solid containers

When a member's `compressed_size` is unknown or zero but the reader exposes a known outer
`compressed_source_size` (the byte length of the compressed container stream — e.g. a
`.tar.gz` file's size on disk), the system SHALL evaluate the decompression ratio during
`extract()` / `extract_all()` as:

```
cumulative_bytes_written / compressed_source_size
```

using the same `max_ratio` limit and `ratio_activation_threshold` (default 5 MiB) as the
per-member ratio check. The check SHALL run in `BombTracker.count()` alongside the
cumulative `max_extracted_bytes` guard. When `compressed_source_size` is `None` (unknown
source size, plain uncompressed container), the archive-wide ratio check is skipped.

Per-member ratio (when `member.compressed_size` is known and greater than zero) and
archive-wide ratio are independent guards; either may trip first.

#### Scenario: compressed tar extract trips archive-wide ratio

- **WHEN** a small `.tar.gz` (known file size) is extracted and cumulative output exceeds
  `max_ratio` times the file size after crossing the activation threshold
- **THEN** `ExtractionError` is raised during extraction

#### Scenario: archive-wide ratio skipped when outer size unknown

- **WHEN** a compressed tar is extracted from a non-seekable pipe with unknown total size
- **THEN** the archive-wide ratio check is not applied
- **AND** the cumulative `max_extracted_bytes` limit still applies

#### Scenario: plain tar has no archive-wide ratio

- **WHEN** a plain `.tar` is extracted
- **THEN** the archive-wide ratio check is not applied (no compressed outer stream)

#### Scenario: ZIP keeps per-member ratio

- **WHEN** a ZIP member with known `compressed_size` is extracted
- **THEN** the per-member ratio check applies as today
- **AND** the archive-wide ratio is not used in place of per-member `compressed_size`
