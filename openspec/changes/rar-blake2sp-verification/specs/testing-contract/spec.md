## ADDED Requirements

### Requirement: BLAKE2sp verification is tested with KATs and a RAR5 oracle

The suite SHALL include BLAKE2sp known-answer tests (reference BLAKE2 vectors) proving
the internal hasher independent of RAR fixtures, and SHALL assert end-to-end that a native
read of a RAR5 BLAKE2sp-only member verifies: an intact member reads clean and a
corrupted-payload member raises `CorruptionError` (not a silent `DIGEST_UNVERIFIABLE`).
The oracle cross-check (`unrar`/`rarfile`) SHALL confirm the intact bytes; oracle-backed
cases SHALL skip when the tool/library is unavailable.

#### Scenario: BLAKE2sp verification

| Case | Expected |
| --- | --- |
| BLAKE2sp known-answer vectors | Internal hasher matches reference digests |
| Intact RAR5 BLAKE2sp-only member, native read | Reads clean; digest verified (no `DIGEST_UNVERIFIABLE`) |
| Corrupted RAR5 BLAKE2sp-only member payload | `CorruptionError` at terminal read |
| `unrar`/`rarfile` unavailable | Oracle cross-check skips; KATs and native read still run |
