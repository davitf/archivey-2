# Packaging and Extras — delta (compression-library evaluation)

## MODIFIED Requirements

### Requirement: Optional extras map to exactly the libraries the code uses

User-facing optional **extras** (`[7z]`, `[zstd]`, `[all]`, …) SHALL list only libraries
that `src/` imports at runtime for that capability; an extra MUST NOT pin a dependency that
no `src/` code path uses. Libraries needed **only by the test suite** — decode oracles
(`rarfile`, `py7zr`) and fixture generators (`ncompress`, and `pyzstd` while it is only used
to *write* zstd fixtures) — SHALL live in the `dev` dependency group, never in a user-facing
extra. The per-codec library choice and its rationale SHALL be recorded in
`docs/library-analysis.md`, the source of truth for why each library is used or rejected.

Applying this now: `python-xz` (imported nowhere — not `src/`, not the tests) SHALL be
removed from `[all]`; `pyzstd` (used only by the dev test oracle to generate fixtures) SHALL
move from `[all]` to the `dev` group — unless the zstd evaluation promotes it to the runtime
`[zstd]` backend, in which case it belongs in that extra.

#### Scenario: no dead optional dependency in a user-facing extra

- **WHEN** the `[all]` extra (or any user-facing extra) is audited against `src/` imports
- **THEN** every pinned package is reachable from some `src/` code path, or it is removed

#### Scenario: a test-only library lives in the dev group, not an extra

- **WHEN** a library is imported only by the test suite (an oracle or a fixture generator), e.g. `rarfile`, `py7zr`, `ncompress`, or fixture-only `pyzstd`
- **THEN** it is declared in the `dev` dependency group and is absent from every user-facing extra

#### Scenario: the zstd extra matches the chosen backend

- **WHEN** the evaluation selects the zstd decode backend
- **THEN** the `[zstd]` extra pins exactly that package (plus any adopted seekable-zstd backend), and `docs/library-analysis.md` records the choice

