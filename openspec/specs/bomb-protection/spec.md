# Decompression Bomb Protection

## Purpose

Decompression bomb protection prevents maliciously crafted archives from exhausting disk space or memory during extraction by tracking cumulative bytes written and the decompression ratio of individual members. It applies exclusively to the extraction path; read and streaming APIs return raw data and leave bomb detection to the caller.

## Requirements

### Requirement: Enforce Cumulative Max-Extracted-Bytes Limit

The system SHALL track the total number of bytes written across all members during a single `extract()` or `extract_all()` call and SHALL raise `ExtractionError` when that cumulative total exceeds `max_extracted_bytes`. The default limit is 2 GiB (2 147 483 648 bytes). The caller MAY override this limit by passing `max_extracted_bytes` to `extract()` or `extract_all()`.

The limit is tracked by a `BombTracker` instance constructed once per extraction call. Byte counts are cumulative across all members in the call, not per-member.

```python
class BombTracker:
    def __init__(self, max_bytes: int, max_ratio: float):
        self._max_bytes = max_bytes
        self._max_ratio = max_ratio
        self._total_bytes = 0

    def count(self, member: Member, chunk_bytes: int) -> None:
        self._total_bytes += chunk_bytes
        if self._total_bytes > self._max_bytes:
            raise ExtractionError(
                f"Extraction limit reached: {self._total_bytes} bytes > {self._max_bytes}"
            )
        if member.compressed_size and member.compressed_size > 0:
            ratio = self._total_bytes / member.compressed_size
            if ratio > self._max_ratio:
                raise ExtractionError(
                    f"Decompression ratio {ratio:.0f}:1 exceeds limit {self._max_ratio:.0f}:1"
                )
```

The default of 2 GiB is sufficient for most legitimate use cases and prevents gigabyte-class bombs.

#### Scenario: cumulative limit exceeded mid-extraction

- **WHEN** the running total of bytes written across all extracted members exceeds `max_extracted_bytes`
- **THEN** `ExtractionError` is raised immediately at the chunk boundary where the limit is crossed
- **AND** extraction halts; no further members are processed

#### Scenario: caller raises the default limit

- **WHEN** `archivey.extract(..., max_extracted_bytes=10 * 2**30)` is called
- **THEN** the enforced cumulative limit is 10 GiB rather than the default 2 GiB

---

### Requirement: Enforce Per-Member Max Decompression Ratio

The system SHALL raise `ExtractionError` when the decompression ratio for a single member exceeds `max_ratio` during extraction. The default ratio limit is 1000:1. The caller MAY override this by passing `max_ratio` to `extract()` or `extract_all()`.

The ratio for a member is computed as `bytes_written_for_member / member.compressed_size`. The check is only performed when `member.compressed_size` is known and greater than zero. The default of 1000:1 is deliberately generous — typical DEFLATE compresses at 3:1 to 10:1, and even pathological quine-style zip bombs produce outer-layer ratios around 391:1 — so the limit catches only pathological cases without triggering on legitimately highly-compressible data.

#### Scenario: single member exceeds ratio limit

- **WHEN** a single member decompresses to more than `max_ratio` times its compressed size
- **THEN** `ExtractionError` is raised while processing that member

#### Scenario: ratio check skipped when compressed size is unknown

- **WHEN** `member.compressed_size` is `None` or `0`
- **THEN** the per-member ratio check is skipped; the cumulative byte limit still applies

#### Scenario: caller lowers the ratio limit

- **WHEN** `archivey.extract(..., max_ratio=100)` is called
- **THEN** any member decompressing at more than 100:1 raises `ExtractionError`

---

### Requirement: Bomb Protection Scope Limited to Extraction Paths

The system SHALL apply decompression bomb limits only during `extract()` and `extract_all()`. The `read()` and `open()` methods on `ArchiveReader` return raw decompressed data without enforcing any byte or ratio limits, leaving bomb detection entirely to the caller.

#### Scenario: read() returns data without bomb check

- **WHEN** `reader.read(member)` is called on a member with an extreme decompression ratio
- **THEN** the raw decompressed bytes are returned to the caller with no `ExtractionError` raised by the library

#### Scenario: open() returns a stream without bomb check

- **WHEN** `reader.open(member)` is called
- **THEN** the returned `BinaryIO` stream delivers decompressed data without enforcing any limit; the caller is responsible for guarding against excessive reads
