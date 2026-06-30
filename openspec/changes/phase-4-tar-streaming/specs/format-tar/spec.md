# TAR Format Behavior — delta (phase-4-tar-streaming)

## ADDED Requirements

### Requirement: Forward-only streaming on non-seekable sources

The TAR backend SHALL support forward-only reading when the archive is opened with
`streaming=True` on a non-seekable source (pipe, socket, or any `BinaryIO` that does not
support `seek`). In this mode the backend MUST NOT call `TarFile.getmembers()` or otherwise
scan the entire archive before yielding the first member; it SHALL walk 512-byte headers
progressively via `tarfile` incremental iteration and yield `(member, stream)` pairs through
`_iter_with_data()` / `stream_members()` with bounded memory.

Random-access open (`streaming=False`) on a non-seekable source SHALL continue to fail fast
with `StreamNotSeekableError`.

#### Scenario: non-seekable compressed tar streams members

- **WHEN** a `.tar.gz` archive is opened with `streaming=True` through a non-seekable wrapper
- **THEN** `stream_members()` yields every member in order and each member's data is readable
- **AND** the underlying source is never `seek()`'d

#### Scenario: non-seekable plain tar iterates forward

- **WHEN** a plain `.tar` is opened with `streaming=True` on a non-seekable source
- **THEN** `for member in ar` yields all members without error
- **AND** `ar.members()` raises `UnsupportedOperationError` (streaming reader is forward-only)

#### Scenario: random-access open on non-seekable tar still fails fast

- **WHEN** a TAR archive is opened with `streaming=False` on a non-seekable source
- **THEN** `StreamNotSeekableError` is raised at open time

### Requirement: Truncation check at end of streaming pass

The system SHALL run the truncation check defined in *Detect truncated TAR archives* at the
end of a forward-only streaming pass as well as after a full random-access scan when
`strict_eof` is configured on open, using the same warn-vs-raise rules.

#### Scenario: truncated streaming tar warns by default

- **WHEN** a truncated `.tar` is consumed via `stream_members()` on a non-seekable source
- **AND** the archive lacks valid end-of-archive null blocks
- **AND** `strict_eof` is `False` (the default)
- **THEN** a `logging.WARNING` is emitted after the last member

#### Scenario: truncated streaming tar raises in strict mode

- **WHEN** the same truncated archive is opened with `strict_eof=True`
- **THEN** `TruncatedError` is raised at the end of the streaming pass
