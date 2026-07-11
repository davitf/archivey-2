# Directory Pseudo-Archive Format Behavior

## Purpose

A plain filesystem directory is presented as a zero-cost pseudo-archive through the unified `ArchiveReader` interface. This allows existing directories to be used interchangeably with real archive formats in conversion pipelines and other workflows that consume an `ArchiveReader`.

## Requirements

### Requirement: Present a filesystem directory as a zero-cost pseudo-archive

The system SHALL open a plain filesystem directory as an `ArchiveReader` with `ArchiveFormat.DIRECTORY`. All files and subdirectories under the given path are enumerated as `ArchiveMember` objects, preserving their filesystem metadata (mode, timestamps, uid, gid). The directory reader is fully seekable and supports direct random access to any member.

#### Scenario: Directory opened as ArchiveReader

- **WHEN** `archivey.open_archive(some_directory_path)` is called and the path is a directory
- **THEN** the returned reader has `format == ArchiveFormat.DIRECTORY`
- **AND** iterating the reader yields one `ArchiveMember` per file and subdirectory found under the path

#### Scenario: ArchiveMember metadata reflects filesystem state

- **WHEN** a directory is opened as an archive
- **THEN** each member's `mode`, `modified`, `uid`, `gid`, `uname`, and `gname` fields are populated from the corresponding filesystem attributes of the underlying path

### Requirement: Report directory format properties

The system SHALL expose the following cost and capability properties for every opened directory archive:

| Property | Value |
|----------|-------|
| Listing cost | O(1) — filesystem directory listing |
| Access cost | DIRECT — each file is independently addressable on the filesystem |
| Supports write | No |
| Requires seek | No (files opened directly from filesystem) |
| Stream capability | SEEKABLE |

#### Scenario: CostReceipt on open

- **WHEN** a directory path is opened with `archivey.open_archive()`
- **THEN** `cost.listing_cost` is `ListingCost.INDEXED`, `cost.access_cost` is `AccessCost.DIRECT`, and `cost.stream_capability` is `StreamCapability.SEEKABLE`

### Requirement: Scan errors are loud, races are tolerated

Genuine directory-walk `OSError`s SHALL continue to propagate unchanged. When a listed
entry or subdirectory vanishes before inspection, the reader SHALL continue and emit
`SCAN_ENTRY_VANISHED` or `SCAN_DIRECTORY_VANISHED` with a JSON-safe relative path and path
kind. These events are reader-operation aggregate data and SHALL not attach to a member
that does not exist.

Under `RAISE`, `DiagnosticRaisedError` SHALL halt the scan. Context SHALL not retain
`DirEntry`, `Path`, exception, or filesystem handle objects.

#### Scenario: entry deleted mid-walk is collected

- **WHEN** an entry disappears between directory listing and `stat` under default policy
- **THEN** it is skipped, `SCAN_ENTRY_VANISHED` is counted/retained/logged on the reader, and the walk continues

#### Scenario: directory race is escalated by policy

- **WHEN** a subdirectory vanishes and `SCAN_DIRECTORY_VANISHED` resolves to `RAISE`
- **THEN** `DiagnosticRaisedError` halts the scan

#### Scenario: permission error remains genuine I/O

- **WHEN** walking a subdirectory raises `PermissionError`
- **THEN** that original error propagates unchanged and no vanished-path diagnostic substitutes for it

### Requirement: Support use in conversion pipelines

The system SHALL allow a directory reader to act as the source in a conversion pipeline via `writer.add_members(reader)`, enabling a directory to be archived into any writable format without intermediate buffering.

#### Scenario: Directory used as conversion source

- **WHEN** a directory reader is passed to `writer.add_members(reader)`
- **THEN** all members from the directory are streamed into the target archive in a single forward pass
- **AND** no intermediate on-disk buffering of the full directory content is required

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
