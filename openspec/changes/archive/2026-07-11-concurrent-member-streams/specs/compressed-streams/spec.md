# Compressed Streams — delta (concurrent-member-streams)

## ADDED Requirements

### Requirement: open_stream is non-seekable by default

The single-stream entry point (`open_stream(...)`-style API) SHALL return a
forward-only stream by default and SHALL accept `seekable: bool = False` to request a
seekable stream. This matches the archive-side rule — no archivey stream is seekable
unless asked — so the seek contract is learned once and applies everywhere.
Concurrency is not a concept for this API (it returns exactly one stream), so it takes
the boolean, not the `MemberStreams` flags enum.

Without `seekable=True`: the returned stream reports `seekable() is False`, `seek()`
raises `io.UnsupportedOperation`, `tell()` works, and no seek index or accelerator is
instantiated. With `seekable=True`: the `seekable-decompressor-streams` contract applies
(native indexes, demand-driven accelerator `AUTO` resolution, loud slow rewinds on the
non-accelerated path).

#### Scenario: default stream is forward-only

- **WHEN** a compressed source is opened through the single-stream API without
  `seekable=True`
- **THEN** the stream reads correctly forward, reports `seekable() is False`, raises
  `io.UnsupportedOperation` on `seek()`, and builds no seek index

#### Scenario: requested seekability activates the seekable-stream contract

- **WHEN** the same source is opened with `seekable=True`
- **THEN** the stream is seekable per `seekable-decompressor-streams`, using native
  indexes or accelerators where available and warning loudly on O(n) rewind fallbacks
