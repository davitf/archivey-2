# Safe Extraction — delta (phase-4-safe-extraction)

## ADDED Requirements

### Requirement: Archive-wide decompression ratio for solid containers

The system SHALL evaluate an archive-wide decompression ratio during `extract()` /
`extract_all()` when a member's `compressed_size` is unknown or zero but the reader exposes a
known outer `compressed_source_size` (the byte length of the compressed container stream —
e.g. a `.tar.gz` file's size on disk), computed as:

```
cumulative_bytes_written / compressed_source_size
```

using the same `max_ratio` limit and `ratio_activation_threshold` (default 5 MiB) as the
per-member ratio check. The check SHALL run in `BombTracker.count()` alongside the
cumulative `max_extracted_bytes` guard. Unlike the per-member ratio (which activates on the
**current member's** output), the archive-wide ratio activates on the **cumulative** output
across the call: it is evaluated only once `_total_bytes` exceeds `ratio_activation_threshold`.
When `compressed_source_size` is `None` (unknown source size, plain uncompressed container),
the archive-wide ratio check is skipped.

The `compressed_source_size` is supplied to the `BombTracker` once per extraction call (the
coordinator reads it from the reader and passes it to the constructor):

```python
class BombTracker:
    def __init__(self, max_bytes: int, max_ratio: float,
                 ratio_activation_threshold: int = 5 * 2**20,  # 5 MiB
                 compressed_source_size: int | None = None):
        ...
        self._compressed_source_size = compressed_source_size

    def count(self, chunk_bytes: int) -> None:
        self._total_bytes += chunk_bytes
        self._member_bytes += chunk_bytes
        if self._total_bytes > self._max_bytes:
            raise ExtractionError(...)                 # cumulative byte guard (unchanged)
        # Per-member ratio: activates on the current member's output (unchanged).
        cs = self._member.compressed_size if self._member else None
        if self._member_bytes > self._ratio_floor and cs and cs > 0:
            if self._member_bytes / cs > self._max_ratio:
                raise ExtractionError(...)
        # Archive-wide ratio: activates on the cumulative output; only when the outer
        # compressed size is known. Independent of the per-member guard above.
        css = self._compressed_source_size
        if self._total_bytes > self._ratio_floor and css and css > 0:
            if self._total_bytes / css > self._max_ratio:
                raise ExtractionError(...)
```

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
