# Access Mode and Cost — delta (concurrent-member-streams)

## ADDED Requirements

### Requirement: Concurrency composes with the two access modes

The existing `streaming: bool = False` parameter remains the only access-mode choice.
Concurrency safety is not a third mode, an `open_archive()` opt-in, or an
`ArchiveyConfig` field:

- **`streaming=False` (random access):** after member materialization, concurrent
  `open()` calls and independent operations on different returned streams are supported.
  Positioning is included only when the individual stream supports it; otherwise normal
  `io.UnsupportedOperation` behavior applies.
  Materialization/iteration/data-pass/extraction/reader-close operations remain
  single-owner and cannot overlap actively executing worker calls. An idle stream lease is
  not active overlap.
- **`streaming=True` (forward-only):** random `open()`/`read()` remain unavailable. The
  existing single progressive pass is exclusive and cannot overlap another pass,
  materialization, extraction invocation, or reader close.

Random-access `stream_members()` is also an exclusive one-pass/data-path operation even
though random `open()` is otherwise available. A caller needing simultaneous streams
SHALL complete `members()`/`scan_members()` and use random `open()`.

Single-owner APIs use explicit operation-owner tokens. `extract_all()` MAY invoke
`stream_members()` and operate on its yielded stream through private child scopes carrying
the extraction token; this is composition, not a second public pass. Unrelated public calls
have no token and remain conflicting even when made reentrantly on the owner thread.

Where the reader detects unsupported overlap, the later operation SHALL raise
`UnsupportedOperationError` before changing state and leave the active operation/stream
usable. Reader operations after `reader.close()` likewise raise
`UnsupportedOperationError`, except that repeated `close()` remains idempotent.

#### Scenario: random-access concurrency needs no extra open argument

- **WHEN** a random-access reader is materialized and workers concurrently open members
- **THEN** the operation is supported without an `allow_multiple_open_streams` keyword

#### Scenario: overlapping pass entry leaves the active pass intact

- **WHEN** one forward/data pass is active and a conflicting pass/random open/close is
  attempted
- **THEN** `UnsupportedOperationError` is raised at the later operation and the original
  iterator/current stream remain usable

#### Scenario: random stream_members and random open do not overlap

- **WHEN** `stream_members()` is active on a random-access reader and `open()` is called
- **THEN** `UnsupportedOperationError` is raised; simultaneous streams are instead
  obtained through the post-materialization random-open seam

#### Scenario: extraction may drive a child pass

- **WHEN** `extract_all()` enters `stream_members()` and reads/closes its yielded streams
- **THEN** the explicit extraction owner token authorizes those child scopes without allowing
  an unrelated public pass

### Requirement: Concurrent-stream cost is informational

The existing `CostReceipt` fields and format values are unchanged.

`access_cost` describes work, including work caused by a simultaneous random-open
schedule. `DIRECT` means members can be reached independently; `SOLID` means a stream may
need to decode earlier bytes in its block. The library SHALL NOT use either value to
permit or deny simultaneous member streams. `solid_block_count` helps a future scheduler
identify independent work units; it is not a concurrency gate.

#### Scenario: cost does not alter concurrent-stream correctness

- **WHEN** materialized `DIRECT` and `SOLID` readers each open multiple member streams
- **THEN** both schedules are supported and byte-correct; only the reported/repeated work
  differs
