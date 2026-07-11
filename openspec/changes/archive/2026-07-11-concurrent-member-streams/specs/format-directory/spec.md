# Format Directory — delta (concurrent-member-streams)

## ADDED Requirements

### Requirement: The directory reader is never more lenient than archive readers

The directory reader exists to make archive-vs-directory code uniform, to exercise the
public API and internals against a trivially inspectable backend, and to serve future
directory↔archive piping (compression sources/sinks). It SHALL therefore enforce every
API-level constraint that archive readers enforce, even where the underlying filesystem
could trivially permit more:

- without `MemberStreams.CONCURRENT`, a second overlapping member stream raises
  `ConcurrentAccessError`, although each member is an independently openable file;
- without `MemberStreams.SEEKABLE`, member streams report `seekable() is False` and
  `seek()` raises `io.UnsupportedOperation`, although the underlying file handle could
  seek.

This is a standing design principle, recorded here so future capability decisions
default to uniformity: code developed against the directory reader MUST behave
identically when pointed at an archive, which is only true if the directory reader
refuses everything an archive reader might refuse.

#### Scenario: directory reader gates concurrency like an archive

- **WHEN** a directory reader opened without `MemberStreams.CONCURRENT` has one member
  stream open and a second member is opened
- **THEN** `ConcurrentAccessError` is raised exactly as it would be for a ZIP or TAR
  reader

#### Scenario: directory member streams are forward-only by default

- **WHEN** a member stream is obtained from a directory reader without declared
  `SEEKABLE`
- **THEN** it reports `seekable() is False` and `seek()` raises
  `io.UnsupportedOperation`, despite being backed by a real file
