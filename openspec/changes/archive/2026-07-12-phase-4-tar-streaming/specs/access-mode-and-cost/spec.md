# Access Mode and Cost — delta (phase-4-tar-streaming)

## ADDED Requirements

### Requirement: Compressed tar opens on non-seekable sources in streaming mode

The system SHALL allow opening a compressed TAR (e.g. `.tar.gz`) from a non-seekable source
when `streaming=True`. The open SHALL succeed and the reader SHALL report
`CostReceipt.stream_capability == StreamCapability.FORWARD_ONLY`. Listing and access costs
remain `REQUIRES_DECOMPRESSION` and `SOLID` respectively — they describe the format layout,
not the source.

#### Scenario: non-seekable tar.gz opens in streaming mode

- **WHEN** a `.tar.gz` archive is opened with `streaming=True` through a `FakeNonSeekable`
  wrapper (or equivalent non-seekable `BinaryIO`)
- **THEN** the open succeeds without `StreamNotSeekableError`
- **AND** `ar.cost.stream_capability == StreamCapability.FORWARD_ONLY`
- **AND** `ar.cost.listing_cost == ListingCost.REQUIRES_DECOMPRESSION`
- **AND** `ar.cost.access_cost == AccessCost.SOLID`

#### Scenario: non-seekable tar.gz rejected in random-access mode

- **WHEN** the same `.tar.gz` is opened with `streaming=False` on a non-seekable source
- **THEN** `StreamNotSeekableError` is raised at open time
