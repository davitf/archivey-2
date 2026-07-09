## ADDED Requirements

### Requirement: Single-reader concurrency contract

A single `ArchiveReader` instance is **not** thread-safe. The system SHALL
document and enforce the following contract:

- **Supported:** in one thread, multiple member streams obtained via `open()`
  (and equivalent member-stream entry points) over a **seekable** source may
  be open at once; interleaved reads SHALL return each member's correct
  bytes. Backends that own the archive file handle directly SHALL obtain
  those streams through the shared-source streamtools primitive (or an
  equivalent lock + per-view cursor), not by unsynchronized seeks on a shared
  raw handle.
- **Unsupported — fail loudly:** using one `ArchiveReader` from two or more
  threads concurrently SHALL raise `UnsupportedOperationError` (or a
  documented subclass) when the misuse is detected, rather than silently
  interleaving I/O. Detection MAY be an owning-thread identity check on
  public I/O entry points.
- **Supported:** two separate `ArchiveReader` instances opened on the same
  path (separate handles) MAY be used freely, including from different
  threads.

This requirement does **not** make readers thread-safe and does **not**
promise parallel multi-threaded extraction.

#### Scenario: interleaved open() streams in one thread

- **WHEN** a seekable archive is opened and two FILE members are each opened
  with `ar.open(...)` without closing the first
- **AND** the caller reads from both handles in an interleaved fashion in the
  same thread
- **THEN** each handle yields that member's correct content

#### Scenario: cross-thread use of one reader fails loudly

- **WHEN** an `ArchiveReader` that has begun I/O on thread A is used for a
  public I/O operation on thread B
- **THEN** the call on thread B raises `UnsupportedOperationError`
- **AND** the error message indicates that readers are bound to a single
  thread
