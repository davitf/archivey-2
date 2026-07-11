# access-mode-and-cost — static cost versus runtime diagnostics

## ADDED Requirements

### Requirement: CostReceipt remains an immutable open-time cost description

`CostReceipt` SHALL continue to describe static access properties known at open time and
SHALL NOT retain runtime diagnostics. In particular, an actual backward seek that
re-decompresses, or a later seek-index construction failure, SHALL be represented in the
reader/stream operation diagnostic aggregate rather than mutating or replacing
`CostReceipt`.

Static `notes` MAY describe a general capability/caveat, but SHALL NOT be used as an
occurrence log or exact counter.

#### Scenario: actual slow rewind is not frozen into cost

- **WHEN** a stream performs a backward seek after the reader was opened
- **THEN** `STREAM_REWIND_REDECOMPRESSES` appears in stream/reader diagnostics and the original `CostReceipt` remains unchanged

#### Scenario: failed optional index does not alter cost receipt

- **WHEN** optional seek-index discovery degrades to sequential decoding at runtime
- **THEN** `SEEK_INDEX_DEGRADED` is counted on the operation aggregate and no diagnostic field is added to `CostReceipt`
