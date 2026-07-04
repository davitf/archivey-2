# Safe Extraction — delta (live-decompression-ratio-guard)

## ADDED Requirements

### Requirement: Live archive-wide decompression ratio for unknown-size streams

The system SHALL evaluate a **live** archive-wide decompression ratio during `extract()` /
`extract_all()` when neither a per-member `compressed_size` nor a cheap outer
`compressed_source_size` is available to serve as a denominator — the case of a `streaming=True`
compressed archive (e.g. a `.tar.gz`) read from a non-seekable pipe, where today only the
cumulative `max_extracted_bytes` cap applies.

The live ratio is computed as:

```
cumulative_bytes_written / compressed_bytes_consumed
```

where `compressed_bytes_consumed` is the running count of compressed bytes pulled from the
archive's outer source (see the `compressed-streams` delta). `BombTracker` SHALL raise
`ExtractionError` once this ratio exceeds `max_ratio`, evaluated only after the cumulative
output (`_total_bytes`) passes `ratio_activation_threshold` (default 5 MiB) — the same limit and
floor as the static ratio checks.

Because compressed bytes cannot be attributed to a single member in a solid or streamed
container, the live ratio is a **cumulative / archive-wide** guard: it extends the existing
archive-wide ratio with a live denominator. It is a global resource guard, so like the
cumulative `max_extracted_bytes` and `max_entries` limits it halts extraction **even under
`OnError.CONTINUE`**.

This guard **complements** the static checks and does not replace them:

- When `member.compressed_size` is known (ZIP), the per-member ratio still applies.
- When `compressed_source_size` is known (a size-probeable compressed archive), the static
  archive-wide ratio applies and the live path is **not** used (no double-counting).
- The live path engages only when both static denominators are absent and a
  `compressed_bytes_consumed` count is available.

Whichever guard has a usable denominator may trip first.

#### Scenario: streaming tar.gz bomb from a pipe is caught by the live ratio

- **WHEN** a highly compressible `.tar.gz` is extracted from a non-seekable pipe (so
  `compressed_source_size` is `None` and TAR members have no `compressed_size`) and its output
  exceeds `max_ratio` times the compressed bytes consumed after crossing the activation threshold
- **THEN** `ExtractionError` is raised during extraction, before the absolute `max_extracted_bytes`
  cap is reached

#### Scenario: live ratio halts even under OnError.CONTINUE

- **WHEN** the live archive-wide ratio is exceeded during a `CONTINUE` extraction
- **THEN** `ExtractionError` is raised and extraction halts regardless of `on_error`

#### Scenario: uncompressed stream does not trip the live ratio

- **WHEN** a plain (uncompressed) `.tar` is extracted from a pipe, so consumed ≈ written (~1:1)
- **THEN** the live ratio never trips; the cumulative `max_extracted_bytes` limit still applies

#### Scenario: known outer size keeps the static archive-wide ratio

- **WHEN** a `.tar.gz` with a cheaply knowable `compressed_source_size` is extracted
- **THEN** the static archive-wide ratio is used and the live path is not engaged (the ratio is
  not counted twice)
