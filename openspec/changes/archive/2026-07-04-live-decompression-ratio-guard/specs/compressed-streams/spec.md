# Compressed Streams — delta (live-decompression-ratio-guard)

## ADDED Requirements

### Requirement: Decompression streams expose compressed bytes consumed

The decompression stream layer SHALL expose a monotonically increasing count of the number of
**compressed bytes consumed from the underlying source** so far, so a caller can compute a live
decompression ratio without knowing the source's total size. The count is surfaced as a running
value (e.g. an `input_bytes_consumed` property) backed by a counting reader wrapping the raw
compressed source; it is incremented as the decompressor pulls input and is available even when
the source is a non-seekable pipe.

The reader SHALL surface the running total for the archive's outer compressed source (parallel
to the cheap-total `compressed_source_size`) as `compressed_bytes_consumed`, returning `None`
when there is no single compressed source to count (an uncompressed container, a directory).
When a member stream is served from the same outer compressed stream (solid / streamed
containers), that member stream's consumption is reflected in the same outer counter — the count
is cumulative across the archive, not reset per member.

The counter SHALL be cheap (an integer incremented on `read()`), and reporting it SHALL NOT
change what bytes are read or decompressed.

#### Scenario: consumed count grows as a compressed stream is read

- **WHEN** a compressed stream (e.g. a `.gz`) is read incrementally from a non-seekable source
- **THEN** the exposed compressed-bytes-consumed count increases monotonically toward the total
  input, and is readable at any point mid-stream

#### Scenario: no counter for an uncompressed or non-stream source

- **WHEN** the archive has no single compressed source (an uncompressed container or a directory)
- **THEN** `compressed_bytes_consumed` is `None`

#### Scenario: reporting the count does not perturb decoding

- **WHEN** the compressed-bytes-consumed count is read repeatedly during extraction
- **THEN** the decompressed output is byte-for-byte identical to reading without observing the count
