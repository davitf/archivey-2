# Archive Reading — delta (shared-source-streams)

## ADDED Requirements

### Requirement: Multiple concurrently-open member streams

The system SHALL support any number of member streams opened from a **single reader** being
held open and read in **interleaved** order without corrupting one another, when they are
served from one underlying source with a single file position. A backend that serves members
by **byte-range access into a shared source** MUST route member reads through a shared-source
view that keeps a **per-view position** and performs each seek+read as an atomic pair under
the source lock, so that reading one open stream never disturbs another open stream's position.

**Scope / carve-out.** This requirement applies to backends that serve members via independent
byte-range views over the source (e.g. the native 7z/RAR readers, ZIP, ISO once adapted,
single-file). A backend that serves members through a **single shared decoder or parser
object** — notably the stdlib-`tarfile`-backed random-access TAR reader — is **exempt**: it MAY
require that only one member stream be open at a time (or serialize opens), and MUST NOT be
expected to support interleaved concurrent opens. A **solid** format (7z folder, RAR block)
satisfies the requirement by giving each `open()` its own decompressor over its own
shared-source view, re-decoding from the block start as the *random open on a solid member*
scenario already permits.

**Single-reader guarantee only.** The reader object itself remains **not thread-safe**: it MUST
NOT be driven (concurrent `open()`, iteration, or `close()`) from multiple threads. That misuse
is **unsupported and undefined** — the reader holds no lock and does not detect it. The
guarantee here is confined to interleaved use of already-opened member streams from one thread.

**Failing loudly on detectable misuse.** Where the shared-source view *can* detect misuse — a
read after the source is closed, or a view whose bounds fall outside the source — the reader
surface SHALL raise a typed error (translated from the primitive's stdlib-shaped error at the
reader boundary; the `streamtools` primitive itself raises `ValueError`/`OSError` and defines
no archivey exception). This "fail loudly" clause covers only detectable primitive misuse, not
the undefined multi-thread-reader case above.

#### Scenario: interleaved reads of two open members stay correct

- **WHEN** two members of a byte-range random-access archive are opened at the same time and
  read in an interleaved sequence of partial reads
- **THEN** each stream returns exactly its own member's bytes in order, regardless of the
  interleaving, and neither is affected by the other's position

#### Scenario: reading after the reader is closed fails loudly

- **WHEN** a member stream is read after its reader (and the underlying source) has been closed
- **THEN** a typed error is raised at the reader surface rather than returning arbitrary or
  empty bytes

#### Scenario: single-decoder backend need not support interleaved opens

- **WHEN** the random-access TAR reader (one shared `tarfile` object) is asked to hold two
  member streams open and interleave them
- **THEN** this is outside the concurrent-open guarantee; the backend MAY serve one member
  stream at a time without violating this requirement
