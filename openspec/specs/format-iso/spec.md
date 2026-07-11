# ISO 9660 Archive Support (via pycdlib)

## Purpose

The system reads ISO 9660 disc images through the `pycdlib` library (optional `[iso]` extra). Because ISO 9660 supports multiple filesystem namespaces — plain ISO 9660, Joliet, and Rock Ridge — the backend auto-selects the richest available namespace and reports the selection so callers can reason about filename and metadata fidelity.

## Requirements

### Requirement: Declare format properties

The system SHALL expose the following properties for the ISO 9660 backend:

| Property | Value |
|----------|-------|
| Backend dependency | `pycdlib` |
| Listing cost | O(1) — directory tree in header region |
| Access cost | DIRECT |
| Supports write | No (pycdlib supports write but out of scope) |
| Requires seek | Yes |

#### Scenario: write attempt on an ISO image

- **WHEN** a caller attempts to create or write an ISO 9660 archive
- **THEN** the system SHALL raise `UnsupportedOperationError`, because ISO write is out of scope for this version

#### Scenario: opening from a non-seekable source

- **WHEN** the source stream does not support seeking
- **THEN** the backend SHALL reject the open with an appropriate error, because `Requires seek` is `True`

---

### Requirement: Auto-select the richest available namespace

The system SHALL automatically select the richest available namespace from the ISO image in the following priority order: Rock Ridge > Joliet > Plain ISO 9660. The selected namespace SHALL be reported in `ArchiveInfo.extra["iso.namespace"]`.

| Namespace | Filename length | Case | POSIX metadata |
|-----------|----------------|------|----------------|
| Rock Ridge | Unlimited | Preserved | Full (mode, uid, gid, symlinks) |
| Joliet | Up to 64 UCS-2 chars | Preserved | None |
| Plain ISO 9660 | 8.3 (level 1) | Upper-case only | None |

#### Scenario: ISO image with Rock Ridge extensions

- **WHEN** an ISO image contains Rock Ridge extensions
- **THEN** the backend SHALL use the Rock Ridge namespace for all member names and metadata
- **AND** `ArchiveInfo.extra["iso.namespace"]` SHALL be `"rock_ridge"`

#### Scenario: ISO image with Joliet extensions but no Rock Ridge

- **WHEN** an ISO image contains Joliet extensions but not Rock Ridge
- **THEN** the backend SHALL use the Joliet namespace for all member names
- **AND** `ArchiveInfo.extra["iso.namespace"]` SHALL be `"joliet"`

#### Scenario: plain ISO 9660 image with no extensions

- **WHEN** an ISO image contains neither Rock Ridge nor Joliet extensions
- **THEN** the backend SHALL use the plain ISO 9660 namespace for all member names
- **AND** `ArchiveInfo.extra["iso.namespace"]` SHALL be `"iso9660"`

---

### Requirement: Reflect namespace-dependent metadata and filename fidelity

The system SHALL surface member metadata according to the capabilities of the selected namespace. Fields that the selected namespace cannot provide SHALL be `None`.

#### Scenario: filename fidelity under Rock Ridge

- **WHEN** the Rock Ridge namespace is active
- **THEN** member names preserve their original case and full length
- **AND** POSIX metadata (`mode`, `uid`, `gid`) and symlinks are available from the Rock Ridge extensions

#### Scenario: filename fidelity under Joliet

- **WHEN** the Joliet namespace is active
- **THEN** member names preserve case and support up to 64 UCS-2 characters
- **AND** `ArchiveMember.mode`, `ArchiveMember.uid`, and `ArchiveMember.gid` SHALL be `None`, because Joliet carries no POSIX metadata

#### Scenario: filename fidelity under plain ISO 9660

- **WHEN** the plain ISO 9660 namespace is active
- **THEN** member names are upper-case and truncated to 8.3 format (level 1 interoperability)
- **AND** `ArchiveMember.mode`, `ArchiveMember.uid`, and `ArchiveMember.gid` SHALL be `None`, because plain ISO 9660 carries no POSIX metadata

---

### Requirement: Read raw `.bin` CD images via a sector-stripping wrapper (lower priority)

The system SHALL support raw `.bin` CD images (bin/cue tracks) — ISO 9660 filesystems
stored in raw 2 352-byte sectors (sync + header + 2 048 bytes of user data + EDC/ECC)
rather than the 2 048-byte logical sectors `pycdlib` expects — by interposing a stream
wrapper that strips each sector down to its 2 048-byte user-data payload and feeds the
unwrapped logical stream to `pycdlib`. This is a **lower-priority** capability: if
supporting it (raw-sector detection, the several common Mode 1 / Mode 2 Form 1 sector
layouts) grows beyond a thin stripping wrapper, this requirement MAY be dropped rather
than carrying disproportionate complexity. A `.cue` sheet is not required; a Mode 1
`.bin` can be detected from its sector sync pattern.

#### Scenario: Mode 1 .bin image

- **WHEN** a raw Mode 1 `.bin` image (2 352-byte sectors) is opened
- **THEN** the backend strips each sector to its 2 048-byte payload and reads the ISO 9660 filesystem through `pycdlib` as if it were a plain `.iso`

#### Scenario: unsupported raw sector layout

- **WHEN** a `.bin` image uses a raw sector layout the stripping wrapper does not handle
- **THEN** the backend raises `UnsupportedFeatureError` rather than misreading the image

---

### Requirement: ISO concurrent member open via locked pycdlib streams

The system SHALL support interleaved concurrent member data streams from one ISO reader
unconditionally, as required by `concurrent-member-streams`. The reader MUST continue to
obtain file member payloads through `pycdlib` (e.g. `open_file_from_iso`), preserving
pycdlib's extent and namespace behavior.

Each `IsoReader` SHALL own one lock covering **every operation on pycdlib's shared image
handle**, including:

- `PyCdlib.open()` / `open_fp()` archive initialization and failure cleanup;
- `open_file_from_iso()` member creation and `PyCdlibIO.__enter__` initialization;
- member `read` and `readinto`, plus `seek`/`tell` where supported;
- member close/context exit;
- archive/PyCdlib close; and
- any other operation found by audit to reposition or close `PyCdlib._cdfp` /
  `PyCdlibIO._fp`.

The lock surrounds the complete pycdlib operation. Archivey buffering/error/lifecycle
wrappers SHALL sit outside the locked layer. Exception translation/stamping, logging,
lifecycle lease release, callbacks, and finalizer hooks SHALL execute after the lock is
released. Library-internal decode inseparable from an atomic handle call MAY execute under
the lock. Unsupported positioning SHALL retain normal `io.UnsupportedOperation` behavior.

For the pinned pycdlib implementation, `walk()` and `get_record()` traverse the parsed
in-memory catalog and do not access `_cdfp`; the materialization operation-owner scope
serializes them, so they do not require the handle lock. The implementation SHALL record and
regression-test that version audit. If a supported pycdlib version adds handle access, the
complete affected call SHALL join the critical section.

This lock guarantees correctness but may serialize I/O; it is not a parallel-throughput
promise. A later independent-image-handle or raw-extent speed claim uses proportionate,
targeted before/after measurements; the baseline has no correctness speed threshold.

#### Scenario: interleaved opens on ISO

- **WHEN** two file members of an ISO image are opened and read interleaved
- **THEN** each stream yields that member's exact bytes in order

#### Scenario: multi-thread opens on ISO

- **WHEN** multiple threads concurrently open and read distinct file members of an ISO
  under `MemberStreams.CONCURRENT` after materialization
- **THEN** each thread yields that member's exact bytes without data races on the shared
  pycdlib handle

#### Scenario: ISO open initialization shares the handle lock

- **WHEN** workers concurrently call member `open()`
- **THEN** `open_file_from_iso` and `PyCdlibIO.__enter__` each execute under the same
  per-reader lock used by subsequent stream operations

#### Scenario: seek, tell, and close cannot race ISO reads

- **WHEN** independent ISO member streams concurrently read/readinto/close and use supported
  positioning
- **THEN** each complete pycdlib operation is serialized under the per-reader lock and
  member positions remain correct

#### Scenario: catalog-only pycdlib calls are audited, not mislabeled

- **WHEN** ISO materialization uses pinned pycdlib `walk()` and `get_record()`
- **THEN** a regression probe confirms they remain in-memory catalog operations under the
  materialization owner scope
- **AND** any supported version that adds `_cdfp` access receives the backend handle lock

#### Scenario: callbacks run after releasing the ISO handle lock

- **WHEN** an ISO operation raises or closes and archivey translates/logs/releases its
  lifecycle lease
- **THEN** that diagnostic/lifecycle work executes without the ISO shared-handle lock held

#### Scenario: ISO lock baseline informs later replacement

- **WHEN** independent handles or raw extent views are proposed to increase throughput
- **THEN** targeted before/after evidence compares relevant wall/lock timing and practical
  seek/byte counters, adding peak memory only if buffering/materialization changes
