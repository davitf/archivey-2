## ADDED Requirements

### Requirement: Shared-source view for concurrent member streams

The stream layer SHALL provide a shared-source primitive (under
`archivey.internal.streams.streamtools`) that exposes multiple independent
read views over one underlying seekable `BinaryIO`. The primitive SHALL:

- Hold a single underlying handle and a lock (`threading.RLock` or equivalent).
- Give each view its own cursor; every `read` / `seek` SHALL reposition the
  underlying handle under the lock before transferring bytes, then update the
  view cursor.
- Allow an optional length bound per view (member-range slicing) without
  requiring a separate unlocked `SlicingStream` over the raw handle.
- Reject creating a view when the underlying stream is not seekable (fail at
  view construction, never silently interleave).
- Keep `streamtools` free of the archivey exception hierarchy (stdlib /
  local errors only; callers translate at the archivey boundary).

Closing a view SHALL NOT close the underlying handle. Closing the shared
source SHALL invalidate outstanding views (subsequent I/O on them fails).

#### Scenario: interleaved reads from two views return correct bytes

- **WHEN** a seekable byte source is wrapped in the shared-source primitive
- **AND** two views are opened at different offsets (or over different ranges)
- **AND** the caller alternates small `read()` calls between the views in one
  thread
- **THEN** each view returns the bytes that correspond to its own cursor /
  range, with no cross-talk

#### Scenario: view creation fails on a non-seekable source

- **WHEN** the shared-source primitive is asked for a second independent view
  (or any view, if the implementation requires seekability up front) over a
  non-seekable underlying stream
- **THEN** view construction fails with a clear error
- **AND** no silent interleaving of reads occurs

#### Scenario: closing the owner invalidates views

- **WHEN** a shared source with an open view is closed
- **THEN** a subsequent `read` or `seek` on that view fails
- **AND** the underlying handle is closed (subject to any documented
  open-view refcount delay, which MUST still end with a closed handle once
  all views are released)
