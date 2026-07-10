# Archive Reading — delta (tar-concurrent-open)

## MODIFIED Requirements

### Requirement: Multiple concurrently-open member streams

The system SHALL support any number of member streams opened from a **single reader** being
held open and read in **interleaved** order without corrupting one another, when they are
served from one underlying source with a single file position. A backend that serves members
by **byte-range access into a shared source** MUST route member reads through a shared-source
view that keeps a **per-view position** and performs each seek+read as an atomic pair under
the source lock, so that reading one open stream never disturbs another open stream's position.

**Scope.** This requirement applies to backends that serve members via independent byte-range
views over the source (e.g. the native 7z/RAR readers, single-file, and the **random-access
TAR reader** when its uncompressed tar stream is seekable). A **solid** format (7z folder,
RAR block, compressed TAR's single compression stream) satisfies the requirement by giving
each `open()` its own logical member view over the shared source (re-decoding or re-seeking
as the format's access cost already permits). Backends whose member addressing is owned by
an external library that already coordinates the shared handle (ISO via `pycdlib`, ZIP via
stdlib `zipfile` — path and stream sources alike, through its `_SharedFile`) are outside
this SharedSource retrofit; they are not listed as non-compliant under this requirement.

**Streaming / non-seekable carve-out.** Forward-only streaming readers (`streaming=True`)
remain out of scope. A random-access TAR whose uncompressed stream is **not** seekable
MUST NOT be expected to support interleaved concurrent opens until a seekable layer exists;
that case is a documented limitation, not a license to skip SharedSource when the stream
*is* seekable.

**Single-reader guarantee only.** The reader object itself remains **not thread-safe**: it MUST
NOT be driven (concurrent `open()`, iteration, or `close()`) from multiple threads. That misuse
is **unsupported and undefined** — the reader holds no lock and does not detect it. The
guarantee here is confined to interleaved use of already-opened member streams from one thread.

**Failing loudly on detectable misuse.** Where the shared-source view *can* detect misuse — a
read after the source is closed — the reader surface SHALL raise a typed error (translated
from the primitive's stdlib-shaped error at the reader boundary; the `streamtools` primitive
itself raises `ValueError`/`OSError` and defines no archivey exception). A view whose
requested bounds extend past the source is **clamped** to the available bytes (like a real
stream), so a truncated archive still yields a short readable view rather than failing at
construction. This "fail loudly" clause covers only detectable primitive misuse (closed
handle), not the undefined multi-thread-reader case above.

#### Scenario: interleaved reads of two open members stay correct

- **WHEN** two members of a byte-range random-access archive are opened at the same time and
  read in an interleaved sequence of partial reads
- **THEN** each stream returns exactly its own member's bytes in order, regardless of the
  interleaving, and neither is affected by the other's position

#### Scenario: reading after the reader is closed fails loudly

- **WHEN** a member stream is read after its reader (and the underlying source) has been closed
- **THEN** a typed error is raised at the reader surface rather than returning arbitrary or
  empty bytes

#### Scenario: random-access TAR with a seekable uncompressed stream supports interleaved opens

- **WHEN** a random-access TAR reader (`streaming=False`) whose uncompressed tar stream is
  seekable has two file members opened at once and those streams are read interleaved
- **THEN** each stream returns its own member's bytes correctly (plain and compressed TAR
  alike when seekable)

#### Scenario: streaming TAR remains outside the concurrent-open guarantee

- **WHEN** a streaming TAR reader (`streaming=True` / forward-only) is asked to hold two
  member streams open and interleave them
- **THEN** this is outside the concurrent-open guarantee; the backend MAY serve one member
  stream at a time without violating this requirement

### Requirement: Random-access member-open is reentrant and reader-state-free

The system SHALL require that, for a **random-access backend that advertises independent member
open** (`streaming=False`, serving members by byte-range access — e.g. the native 7z/RAR
readers, ZIP, single-file, and random-access TAR when the uncompressed stream is seekable),
the member-open implementation (`_open_member`) is a function of the member and the archive's
shared source only: it MUST NOT mutate shared reader state, and it MUST NOT keep per-open
scratch on the reader that a second concurrent open would overwrite. Any byte-range access it
performs MUST go through a shared-source view (per the *Multiple concurrently-open member
streams* requirement) rather than by seeking a single shared handle directly. Backends MAY
hold immutable, already-materialized state (the member list and name index) read-only.

**Scope.** This invariant does **not** apply to forward-only/streaming reads (`streaming=True`),
which are inherently single-pass. Backends whose member addressing is owned by an external
library that already coordinates the shared handle (ISO via `pycdlib`, ZIP path-source via
stdlib `zipfile`) are outside the SharedSource retrofit and are not required to route opens
through an archivey shared-source view; they remain subject to the no-per-open-scratch rule
on archivey-owned reader state. Random-access TAR with a non-seekable uncompressed stream is
outside the independent-open advertisement until a seekable layer exists. It is a
forward-compatibility contract that keeps the reader ABC ready for a future parallel-extraction
consumer without an interface retrofit; it does not by itself make the reader object
thread-safe, and it imposes no ordering or performance guarantee.

**Materialize-before-fan-out.** A future concurrent consumer MUST materialize the member list
(a completed random-access pass) before opening members concurrently; the one-time member-cache
build is not itself concurrency-safe. This precondition is documented on the ABC now so Phase 6
backends and any future consumer honor it.

#### Scenario: opening one member does not disturb another open

- **WHEN** a random-access backend that advertises independent member open serves two members
  opened concurrently from the same reader
- **THEN** neither open call has mutated reader state the other depends on, and each returned
  stream reads its own member's bytes correctly under interleaving

#### Scenario: member-open derives access from the shared source

- **WHEN** such a backend implements `_open_member`
- **THEN** it obtains the member's byte range through a shared-source view rather than by
  seeking a single shared handle in place, so concurrent opens cannot corrupt each other's
  position

#### Scenario: streaming backends and non-seekable TAR are out of scope; ISO is library-owned

- **WHEN** the reader is a forward-only streaming pass
- **THEN** this invariant does not apply and the backend is not required to support concurrent
  member opens
- **WHEN** the backend is random-access TAR whose uncompressed stream is not seekable
- **THEN** independent concurrent member open is not advertised until a seekable layer exists
- **WHEN** the backend is ISO (`pycdlib` owns member addressing)
- **THEN** it is not required to route opens through an archivey shared-source view and is not
  listed as non-compliant; archivey-owned reader state still MUST NOT hold per-open scratch
