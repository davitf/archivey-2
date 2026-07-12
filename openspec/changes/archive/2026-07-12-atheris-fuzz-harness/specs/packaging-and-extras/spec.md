## ADDED Requirements

### Requirement: CI-only fuzz dependency group

The system SHALL provide a PEP 735 dependency group named `fuzz` that installs
`atheris` (and any harness-only helpers it needs) for coverage-guided fuzz CI.
The `fuzz` group is **not** a user-facing runtime extra: it MUST NOT appear in
`[all]`, `[recommended]`, `[recommended-lite]`, or any format/codec/CLI extra,
and MUST NOT be required to import or use `archivey` at runtime.

#### Scenario: fuzz packaging matrix

| Case | Expected |
| --- | --- |
| `pip install archivey` / `archivey[all]` | `atheris` not installed |
| Fuzz CI job | Installs via `uv sync --group fuzz` (plus target runtime needs) |
| Runtime import of `archivey` without fuzz group | No `atheris` dependency |

## MODIFIED Requirements

### Requirement: Optional extras map only to libraries the code uses

User-facing extras SHALL list only libraries imported by `src/` at runtime for that
capability. A package used only by tests, decode oracles, fixture generation, or
fuzz harnesses MUST live in a PEP 735 dependency group (`dev`, `fuzz`, …) and be
absent from every user-facing extra.

The per-codec library choice and rationale SHALL be recorded in
`docs/internal/library-analysis.md`. A guard test or check script SHALL prevent dead or
test-only dependencies from returning to user-facing extras. A dependency pinned
ahead of its implementation phase, such as `[cli]` or `[7z-write]`, is permitted only
through an explicit documented allowlist in that guard.

#### Scenario: dependency-audit matrix

| Case | Expected |
| --- | --- |
| User-facing extra audited against `src/` imports | Every pinned package is reachable from runtime code or explicitly allowlisted |
| Library imported only by tests (`rarfile`, oracle `py7zr`, `ncompress`, fixture-only `pyzstd`) | Declared in `dev`; absent from runtime extras |
| `atheris` | Declared in `fuzz` group; absent from runtime extras and `[all]` |
| `pip install archivey[zstd]` on Python 3.11-3.13 | Installs `backports.zstd`; does not pull `zstandard` |
| `pip install archivey[zstd]` on Python 3.14+ | No third-party zstd package required; stdlib `compression.zstd` provides the backend |
| Extra lists a library no `src/` module imports and not allowlisted | Packaging audit fails |
