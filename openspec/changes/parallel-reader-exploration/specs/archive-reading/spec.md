# Archive Reading — delta (parallel-reader-exploration)

## ADDED Requirements

### Requirement: Random-access member-open is reentrant and reader-state-free

The system SHALL require that, for a **random-access backend that advertises independent member
open** (`streaming=False`, serving members by byte-range access — e.g. the native 7z/RAR
readers, ZIP, single-file), the member-open implementation (`_open_member`) is a function of the
member and the archive's shared source only: it MUST NOT mutate shared reader state, and it MUST
NOT keep per-open scratch on the reader that a second concurrent open would overwrite. Any
byte-range access it performs MUST go through a shared-source view (per the *Multiple
concurrently-open member streams* requirement) rather than by seeking a single shared handle
directly. Backends MAY hold immutable, already-materialized state (the member list and name
index) read-only.

**Scope.** This invariant does **not** apply to forward-only/streaming reads (`streaming=True`),
which are inherently single-pass, nor to backends exempted from concurrent-open (a single shared
decoder/parser object, such as the random-access TAR reader). It is a forward-compatibility
contract that keeps the reader ABC ready for a future parallel-extraction consumer without an
interface retrofit; it does not by itself make the reader object thread-safe, and it imposes no
ordering or performance guarantee.

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

#### Scenario: streaming and single-decoder backends are out of scope

- **WHEN** the reader is a forward-only streaming pass, or a backend served by a single shared
  decoder object (random-access TAR)
- **THEN** this invariant does not apply and the backend is not required to support concurrent
  member opens
