# 0011 — Zero-dependency core

- **Status:** accepted
- **Date:** project founding constraint
- **Provenance:** `VISION.md`; OpenSpec `packaging-and-extras`; maintenance reality notes

## Context

Optional native wheels and format libraries complicate installs (especially Windows /
locked-down environments). The library aims to feel stdlib-adjacent.

## Decision

Bare `pip install archivey` has **no** third-party runtime dependencies. Optional
capabilities use named extras. System `unrar` is a non-pip requirement for RAR **data**
only.

## Consequences

- Core ZIP/TAR/gz/bz2/xz/directory/(common) 7z / RAR listing always available.
- CI matrix includes a core-only leg; extras must degrade with clear errors.
