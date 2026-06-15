# Directory Pseudo-Archive Format Behavior

## Purpose

A plain filesystem directory is presented as a zero-cost pseudo-archive through the unified `ArchiveReader` interface. This allows existing directories to be used interchangeably with real archive formats in conversion pipelines and other workflows that consume an `ArchiveReader`.

## Requirements

### Requirement: Present a filesystem directory as a zero-cost pseudo-archive

The system SHALL open a plain filesystem directory as an `ArchiveReader` with `ArchiveFormat.DIRECTORY`. All files and subdirectories under the given path are enumerated as `Member` objects, preserving their filesystem metadata (mode, timestamps, uid, gid). The directory reader is fully seekable and supports direct random access to any member.

#### Scenario: Directory opened as ArchiveReader

- **WHEN** `archivey.open_archive(some_directory_path)` is called and the path is a directory
- **THEN** the returned reader has `format == ArchiveFormat.DIRECTORY`
- **AND** iterating the reader yields one `Member` per file and subdirectory found under the path

#### Scenario: Member metadata reflects filesystem state

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
- **THEN** `cost.listing_cost` is `ListingCost.O1`, `cost.access_cost` is `AccessCost.DIRECT`, and `cost.stream_capability` is `StreamCapability.SEEKABLE`

### Requirement: Support use in conversion pipelines

The system SHALL allow a directory reader to act as the source in a conversion pipeline via `writer.add_members(reader)`, enabling a directory to be archived into any writable format without intermediate buffering.

#### Scenario: Directory used as conversion source

- **WHEN** a directory reader is passed to `writer.add_members(reader)`
- **THEN** all members from the directory are streamed into the target archive in a single forward pass
- **AND** no intermediate on-disk buffering of the full directory content is required
