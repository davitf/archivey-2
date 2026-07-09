# Archive Reading — delta (shared-source-streams)

## ADDED Requirements

### Requirement: Multiple concurrently-open member streams

The system SHALL support any number of member streams opened from a **single reader** being
held open and read in **interleaved** order without corrupting one another, even when they are
served from one underlying source with a single file position. A backend that serves members
by seeking within a shared source MUST route member reads through a shared-source view that
keeps a **per-view position** and performs each seek+read as an atomic pair, so that reading
one open stream never disturbs another open stream's position.

This is a single-reader guarantee. The reader object itself remains **not thread-safe**: it
MUST NOT be driven (concurrent `open()`, iteration, or `close()`) from multiple threads. The
guarantee is confined to already-opened member streams. Where the shared-source view can
detect misuse — a read after the source is closed, or a view whose bounds fall outside the
source — it SHALL raise a typed error rather than return silently wrong bytes.

The underlying-source lock that makes seek+read atomic also makes reading already-opened
member streams from different threads **data-correct** (serialized, not parallel). Genuine
parallel reading is **not** a supported v1 performance path and carries no such promise; it is
addressed separately.

#### Scenario: interleaved reads of two open members stay correct

- **WHEN** two members of a random-access archive are opened at the same time and read in an
  interleaved sequence of partial reads
- **THEN** each stream returns exactly its own member's bytes in order, regardless of the
  interleaving, and neither is affected by the other's position

#### Scenario: reading after the reader is closed fails loudly

- **WHEN** a member stream is read after its reader (and the underlying source) has been closed
- **THEN** a typed error is raised rather than returning arbitrary or empty bytes

#### Scenario: the reader object is still single-thread

- **WHEN** documentation or callers consider using one reader from multiple threads
- **THEN** the contract is that the reader is one-per-thread; only already-opened member
  streams are safe to read concurrently, and then only data-correctly (serialized), not in
  parallel
