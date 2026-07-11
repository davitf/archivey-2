# Format ISO — delta (tar-concurrent-open)

## ADDED Requirements

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
