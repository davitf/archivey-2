## MODIFIED Requirements

### Requirement: Reading member data

The public `ArchiveStream` SHALL implement the `BinaryIO` contract, remain caller-closed,
and additionally expose an immutable operation-filtered diagnostic snapshot:

```python
class ArchiveStream(BinaryIO):
    @property
    def diagnostics(self) -> DiagnosticSummary: ...

def read(self, member: str | ArchiveMember) -> bytes: ...
def open(self, member: str | ArchiveMember) -> ArchiveStream: ...
```

Both methods accept a name or an `ArchiveMember` yielded by this reader. An unknown name
raises `KeyError`; a foreign member raises `ValueError`. `read()` materializes the entire
payload without extraction bomb checks and is intended for small trusted members.
`open()` streams in bounded chunks. Full reads verify any supported member digest;
streaming verification raises `CorruptionError` on the terminal read only after all valid
chunks have been delivered, while `read()` raises without returning bytes.

After symlink/hardlink following (see the link-following requirement), if the **resolved**
member is not `MemberType.FILE`, `open()` and `read()` SHALL raise `ArchiveyUsageError`.
They MUST NOT return an empty byte stream for directories, anti-items, `OTHER` members, or
unresolved non-file targets. (`stream_members` continues to yield `None` for non-file
members — see that requirement.)

A reader-owned stream SHALL use an operation token/watermark over the reader's collector.
It SHALL NOT own or retain a second copy of its diagnostics. A standalone
`ArchiveStream` not owned by a reader SHALL own one stream-lifetime collector.

#### Scenario: opening a member returns the diagnostic stream type

- **WHEN** `reader.open("data.bin")` succeeds
- **THEN** it returns an `ArchiveStream` usable as `BinaryIO`, and `stream.diagnostics` reports only that stream operation's events

#### Scenario: stream and reader do not duplicate retention

- **WHEN** a reader-owned stream emits a rewind diagnostic
- **THEN** stream and reader snapshots can both expose it while the shared collector retains and charges it only once

#### Scenario: reading member as bytes

- **WHEN** `ar.read("readme.txt")` is called
- **THEN** the full uncompressed content is returned as `bytes`

#### Scenario: opening a member from a different reader is rejected

- **WHEN** `ar.open(member)` is called with an `ArchiveMember` yielded by a *different* reader
- **THEN** `ValueError` is raised (never data from the wrong entry)

#### Scenario: opening a directory member raises

- **WHEN** `ar.open(directory_member)` or `ar.read(directory_member)` is called
- **THEN** `ArchiveyUsageError` is raised (never an empty byte stream, raw `IsADirectoryError`, or format `CorruptionError`)

#### Scenario: opening an anti-item raises

- **WHEN** `ar.open(anti_member)` or `ar.read(anti_member)` is called for a `MemberType.ANTI` member
- **THEN** `ArchiveyUsageError` is raised

#### Scenario: opening OTHER raises

- **WHEN** `ar.open(other_member)` or `ar.read(other_member)` is called for a `MemberType.OTHER` member
- **THEN** `ArchiveyUsageError` is raised

## ADDED Requirements

### Requirement: Non-file members yield no stream in stream_members

When `stream_members` yields a member whose `is_file` is `False` (including
`DIRECTORY`, `SYMLINK`, `HARDLINK`, `OTHER`, and `ANTI`), the paired stream SHALL be
`None`. Callers MUST NOT receive an empty `ArchiveStream` for those members.

#### Scenario: directory has no stream

- **WHEN** `stream_members` yields a directory member
- **THEN** the stream is `None`

#### Scenario: anti-item has no stream

- **WHEN** `stream_members` yields a `MemberType.ANTI` member
- **THEN** the stream is `None`
