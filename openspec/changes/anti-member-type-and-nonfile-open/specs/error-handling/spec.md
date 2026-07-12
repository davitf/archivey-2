## MODIFIED Requirements

### Requirement: Usage errors are a separate hierarchy from ArchiveyError

The system SHALL define `ArchiveyUsageError(Exception)` — deliberately **not** an
`ArchiveyError` subclass — as the root for errors that indicate a bug in the calling
code rather than a property of the archive, the environment, or a supported limitation.
`except ArchiveyError` is what applications wrap archive handling in; it MUST NOT
swallow caller misuse.

`ConcurrentAccessError(ArchiveyUsageError)` SHALL be raised when a second member stream
is opened while another is live on a reader without `MemberStreams.CONCURRENT`. Its
message SHALL include the recorded `open_archive()` call site (`file:line`) so the error
points at where the capability should have been declared.

`ArchiveyUsageError` (the root, or a future subclass) SHALL cover the other detected
misuse states:

- a reader-wide single-owner pass overlapping another distinct pass (`__iter__` /
  `stream_members` / `extract_all`) or overlapping active worker calls;
- without `MemberStreams.CONCURRENT`, a reader close overlapping an actively executing
  member-worker call (an idle leased stream is not overlap; under `CONCURRENT`, close
  drains workers instead);
- any new reader operation/property except repeated `close()` / `__exit__` after
  `reader.close()`;
- same-reader password-provider reentry into a password-requiring operation that would
  deadlock;
- opening/using an `ArchiveMember` that does not belong to this reader (wrong-reader
  identity);
- member I/O after the caller closed its own supplied source early; and
- `open()` / `read()` of a resolved non-file member (`DIRECTORY`, `ANTI`, `OTHER`, or a
  link that did not resolve to a `FILE`) — asking for payload that does not exist.

The error SHALL be raised at the later operation before it changes state and MUST leave
the earlier operation/stream usable.

`open_archive(streaming=True, member_streams=…CONCURRENT…)` SHALL raise
`ArchiveyUsageError` (invalid access-mode/capability combination).

Boundaries of the hierarchy:

- `UnsupportedOperationError` and `UnsupportedFeatureError` remain `ArchiveyError`s:
  they describe what an archive, format, backend, or access mode cannot provide — an
  input/environment property, not a caller bug.
- Stream-level conventions stay stdlib-shaped and are not archivey taxonomy: I/O on a
  **closed stream** raises `ValueError`; unsupported positioning raises
  `io.UnsupportedOperation` (this is also how undeclared `SEEKABLE` surfaces, because
  seek-probing consumers already check `seekable()`/catch that type).

Internal operation-owner children are not overlap: materialization/worker link reads,
`extract_all()` member/counter peeks and owned `stream_members()` passes, and I/O/close
on a pass's yielded stream carry the root token explicitly. Reentrant public calls do
not inherit that token implicitly and remain rejectable.

#### Scenario: open on a directory is a usage error

- **WHEN** `ar.open(directory_member)` is called
- **THEN** `ArchiveyUsageError` is raised (not `ArchiveyError`, not a raw OS error)

#### Scenario: ConcurrentAccessError remains a usage error

- **WHEN** a second member stream is opened without `MemberStreams.CONCURRENT`
- **THEN** `ConcurrentAccessError` (an `ArchiveyUsageError`) is raised
