# Archive Reading — delta (parallel-reader-exploration)

## ADDED Requirements

### Requirement: Backend member-open is reentrant and reader-state-free

A backend's member-open implementation (`_open_member`) SHALL be a function of the member and
the archive's shared source only: it MUST NOT mutate shared reader state, and it MUST NOT keep
per-open scratch on the reader that a second concurrent open would overwrite. Any byte-range
access it performs SHALL go through a shared-source view (per the *Multiple concurrently-open
member streams* requirement) rather than by seeking a single shared handle directly. Backends
MAY hold immutable, already-materialized state (the member list and name index) read-only.

This is a forward-compatibility contract that keeps the reader ABC ready for a future
parallel-extraction consumer without an interface retrofit; it does not by itself make the
reader object thread-safe, and it imposes no ordering or performance guarantee.

#### Scenario: opening one member does not disturb another open

- **WHEN** a backend serves two members opened concurrently from the same reader
- **THEN** neither open call has mutated reader state the other depends on, and each returned
  stream reads its own member's bytes correctly under interleaving

#### Scenario: member-open derives access from the shared source

- **WHEN** a backend implements `_open_member` for a random-access format
- **THEN** it obtains the member's byte range through a shared-source view rather than by
  seeking a single shared handle in place, so concurrent opens cannot corrupt each other's
  position
