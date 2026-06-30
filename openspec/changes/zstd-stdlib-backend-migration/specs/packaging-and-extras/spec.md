# Packaging and Extras — delta (zstd stdlib backend migration)

## MODIFIED Requirements

### Requirement: Optional extras map to exactly the libraries the code uses

User-facing optional **extras** SHALL list only libraries that `src/` imports at runtime for
that capability. The `[zstd]` extra SHALL pin the chosen zstd decode backend: `backports.zstd`
constrained to `python_version < "3.14"` (on 3.14+ the stdlib `compression.zstd` is used, so no
runtime dependency is needed), replacing the previous `zstandard` pin. The `[7z]` bundle's zstd
dependency SHALL follow the same backend. `zstandard` SHALL NOT be pinned in a runtime extra
once the swap lands (it may return to `[all]` later as an alternative backend behind its own
extra). Test-only oracles and fixture generators remain in the `dev` group. The per-codec choice
and rationale are recorded in `docs/library-analysis.md`.

#### Scenario: the zstd extra pins the stdlib-line backend

- **WHEN** `pip install archivey[zstd]` is run on Python 3.11–3.13
- **THEN** `backports.zstd` is installed and `.zst` / `.tar.zst` reading works via the `compression.zstd` API
- **AND** `zstandard` is not pulled in

#### Scenario: no zstd runtime dependency on Python 3.14+

- **WHEN** `pip install archivey[zstd]` is resolved on Python 3.14 or newer
- **THEN** no third-party zstd package is required, because the standard-library `compression.zstd` provides the backend
