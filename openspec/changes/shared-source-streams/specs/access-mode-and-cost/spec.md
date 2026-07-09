## ADDED Requirements

### Requirement: Concurrent member views require a seekable source

The system SHALL refuse a second concurrent member view when
`CostReceipt.stream_capability` is `FORWARD_ONLY` (or the source is otherwise
non-seekable). Independent concurrently-open member streams (multiple live
`open()` handles on one reader) require repositioning the underlying archive
source; the second `open()` MUST raise `UnsupportedOperationError` rather than
interleaving reads on a single forward cursor.

A single forward pass via `__iter__` / `stream_members` / `extract_all` on a
`FORWARD_ONLY` reader remains the supported access pattern for that source.

#### Scenario: second open on a forward-only reader fails

- **WHEN** a reader reports `stream_capability == StreamCapability.FORWARD_ONLY`
- **AND** the caller successfully opens one member stream (where the backend
  permits) and then calls `open()` on another FILE member without closing the
  first
- **THEN** the second `open()` raises `UnsupportedOperationError`
- **AND** no silent mixing of the two members' bytes occurs
