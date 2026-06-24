# Packaging and Extras — delta (compression-library evaluation)

## MODIFIED Requirements

### Requirement: Optional extras map to exactly the libraries the code uses

The extras → capability mapping SHALL list only libraries the library actually imports; an
extra MUST NOT pin a dependency that no code path in `src/` uses. The per-codec library
choice and its rationale SHALL be recorded in `docs/library-analysis.md`, which is the
source of truth for why each library is used or rejected. The currently-dead `python-xz`
and `pyzstd` pins in the `[all]` extra SHALL be removed (or wired up if the evaluation
decides to use them).

#### Scenario: no dead optional dependency

- **WHEN** the `[all]` extra (or any extra) is audited against `src/` imports
- **THEN** every pinned package is reachable from some code path, or it is removed

#### Scenario: the zstd extra matches the chosen backend

- **WHEN** the evaluation selects the zstd decode backend
- **THEN** the `[zstd]` extra pins exactly that package (plus any adopted seekable-zstd backend), and `docs/library-analysis.md` records the choice
