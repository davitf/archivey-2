## MODIFIED Requirements

### Requirement: Sequential in-order iteration

The system SHALL support iterating all members in archive order via `__iter__`, MAY
materialize the full member list via `members()`, and SHALL provide `scan_members()` —
a mode-agnostic way to obtain the fully-resolved member list.

```python
def __iter__(self) -> Iterator[ArchiveMember]: ...     # sequential, in-order
def members(self) -> list[ArchiveMember]: ...          # materializes all (random-access only; may scan)
def scan_members(self) -> list[ArchiveMember]: ...      # fully-resolved list, either mode
def get_members_if_available(self) -> list[ArchiveMember] | None: ...  # index-only peek
```

`__iter__` MUST yield `ArchiveMember` objects one at a time in archive order without
loading all members into memory. In **random-access** mode, `members()` MAY trigger a full
scan for streaming formats that have no central directory, and after the member list has
been materialized once, subsequent `__iter__` calls MUST return from the cache rather than
re-reading the archive. In **streaming** mode there is no such cache-replay: `__iter__` is
part of the single forward pass and is single-use (see the forward-only requirement below
and `access-mode-and-cost`).

`scan_members()` SHALL return the **fully-resolved** member list — every link's
`link_target_member` filled where the target exists, including forward-pointing symlinks
and true last-wins symlinks. In random-access mode it is equivalent to `members()`. On a
`streaming=True` reader it SHALL return the cache if the single forward pass has already
completed, otherwise **finish that pass** — running it from the start, or draining the
remainder of an *interrupted* one — resolving all links, and returning the complete list.
`scan_members()` is the only method permitted after an iteration method has started;
running it consumes/finishes the pass (see `access-mode-and-cost`).

During a live forward pass a forward-pointing symlink is unresolved at the moment it is
yielded (a single pass cannot see ahead); the system finalizes resolution when the pass
reaches its end. Completing a forward pass — via `__iter__`, `stream_members`,
`extract_all`, or `scan_members` — SHALL finalize the fully-resolved member cache: the
system runs full link resolution over all members (filling forward-pointing and last-wins
symlink targets **in place** on the objects already yielded, per the mutable-member
contract) and records the list so `get_members_if_available()` returns it thereafter. A
forward pass abandoned before completion (an early `break`, with no subsequent
`scan_members()`) SHALL NOT finalize the cache.

The reader deliberately defines **no `__len__`** (and no `__getitem__` — see the
name-lookup requirement): the reader is not a collection, and the sequence/mapping
protocols get probed *implicitly* in ways the library does not control (`list(reader)`
probes `__len__` for preallocation via the length-hint protocol). `len(reader)` therefore
raises Python's own `TypeError` in every mode; a caller that wants a count uses
`len(ar.members())`, `ar.info.member_count` (when cheaply known), or counts during
iteration. `list(reader)` just iterates.

`get_members_if_available()` is **index-only**: it returns the member list only when it is
available without scanning and without reading member data (an upfront index, or an
already-materialized cache), else `None`; it never scans and never begins the forward pass,
so it is callable under any intent. Because it reads no member data, members it returns may
have unresolved links for formats that store link targets in member data (see
`access-mode-and-cost` for its full contract).

When opened with `streaming=True`, the reader is forward-only: `members()`, `get()`,
`open()`, and `read()` all SHALL raise `UnsupportedOperationError` (uniformly, not
depending on a loaded index). Only a single forward pass — `__iter__`/`stream_members` or
one `extract_all` — is allowed, with `scan_members()` permitted to finish or return it, and
`get_members_if_available()` callable at any time. See the access mode × method table in
`access-mode-and-cost`.

#### Scenario: forward iteration

- **WHEN** `for member in ar` is executed
- **THEN** the reader yields `ArchiveMember` objects in archive order without buffering all of them in memory

#### Scenario: materialization on a streaming reader

- **WHEN** `ar.members()` is called on a reader opened with `streaming=True`
- **THEN** `UnsupportedOperationError` is raised

#### Scenario: streaming iteration is single-use

- **WHEN** a `streaming=True` reader is iterated once via `__iter__` (to completion or with an early `break`), then any of `__iter__` / `stream_members` / `extract_all` is called again
- **THEN** `UnsupportedOperationError` is raised (there is no streaming cache-replay of `__iter__`)

#### Scenario: scan_members() on a streaming reader returns the fully-resolved list

- **WHEN** `ar.scan_members()` is called on a not-yet-iterated `streaming=True` reader of a no-index format (e.g. a streaming tar) whose archive contains a symlink pointing at a *later* member
- **THEN** the full member list is returned with that forward-pointing symlink's `link_target_member` resolved
- **AND** the single forward pass is now consumed, so a subsequent `stream_members()` / `__iter__` / `extract_all` raises `UnsupportedOperationError`

#### Scenario: scan_members() finishes an interrupted iteration

- **WHEN** a `streaming=True` reader of a no-index format is iterated with an early `break`, then `ar.scan_members()` is called
- **THEN** it drains the remainder of the single pass internally and returns the complete, fully-resolved member list

#### Scenario: scan_members() equals members() in random-access mode

- **WHEN** `ar.scan_members()` is called on a `streaming=False` reader
- **THEN** it returns the same fully-resolved member list as `ar.members()`, and the reader remains usable for random access (nothing is consumed)

#### Scenario: get_members_if_available() returns the list after a completed streaming pass

- **WHEN** a `streaming=True` reader of a no-index format is iterated to completion via `__iter__` (or `stream_members`), then `ar.get_members_if_available()` is called
- **THEN** the fully-resolved member list is returned (not `None`), and any forward-pointing symlink resolved during finalization is now visible on the objects yielded during the pass

#### Scenario: abandoned partial pass does not materialize

- **WHEN** a `streaming=True` reader of a no-index format is iterated but the loop `break`s before the last member and `scan_members()` is not called, then `ar.get_members_if_available()` is called
- **THEN** `None` is returned (the pass did not complete, so no full listing is claimed)

#### Scenario: no len(); list() iterates

- **WHEN** `len(ar)` is called on any reader
- **THEN** Python's own `TypeError` is raised (`ArchiveReader` defines no `__len__`)
- **AND** `list(ar)` iterates normally (on a streaming reader, consuming the single forward pass)
