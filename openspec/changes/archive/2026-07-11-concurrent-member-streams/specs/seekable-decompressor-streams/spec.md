# Seekable Decompressor Streams — delta (concurrent-member-streams)

## ADDED Requirements

### Requirement: Seek machinery is demand-driven

Seek support SHALL be constructed only for streams whose seekability was declared —
`MemberStreams.SEEKABLE` on `open_archive()`, or `seekable=True` on the single-stream
API. For undeclared streams the system SHALL NOT parse XZ footers or lzip trailers for
random access, SHALL NOT instantiate `rapidgzip` / indexed-bzip2 accelerators, and SHALL
NOT retain rewind buffers or seek-point tables; the stream is forward-only
(`seekable() is False`, `seek()` raises `io.UnsupportedOperation`).

The `use_rapidgzip` / `use_indexed_bzip2` tri-state (`AUTO`/`ON`/`OFF`) configuration
resolves `AUTO` against **declared seek demand** rather than the access-mode proxy:
`AUTO` + declared seekability + library available → accelerator used; `AUTO` without
declared seekability → accelerator not instantiated. `ON`/`OFF` retain their explicit
meanings for declared-seekable streams; `ON` with an undeclared stream has nothing to
accelerate and creates no accelerator.

For **declared-seekable** streams the existing contract is unchanged: native-index
formats (XZ, lzip) seek by decompressing only the needed blocks; accelerator-backed
gzip/bzip2 seek via their indexes; and the stdlib fallback's O(n)-per-rewind seek is
permitted but MUST NOT be silent (the warning naming the `[seekable]` accelerator
remains).

#### Scenario: undeclared stream builds no seek machinery

- **WHEN** a gzip/xz/bzip2/lzip stream is opened without declared seekability under
  `AUTO` accelerator configuration
- **THEN** no index is parsed, no accelerator is instantiated, and the stream is
  forward-only

#### Scenario: declared stream resolves AUTO to the accelerator

- **WHEN** the same stream is opened with declared seekability, `AUTO` configuration,
  and the accelerator library installed
- **THEN** the accelerator (or native index) provides random access without full
  re-decompression

#### Scenario: declared stream without accelerator still warns on slow rewinds

- **WHEN** a declared-seekable gzip stream has no accelerator available and the caller
  seeks backward
- **THEN** the seek succeeds by re-decompressing from the start and a warning names the
  `[seekable]` accelerator
