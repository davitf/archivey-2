## ADDED Requirements

### Requirement: Native 7z restructure preserves the behavioral suite

A structural refactor of the native 7z backend (single coder registry, two-phase
header parse, plan/execute folder pipeline) SHALL NOT change caller-visible 7z
behavior. The existing native 7z automated suite is the preservation gate: tests
under `tests/test_sevenzip_*.py`, the 7z entries in the declarative corpus and
`py7zr` / `7z` oracle cross-checks, password/encryption coverage, and codec-chain
coverage MUST stay green without weakening assertions or deleting cases to mask a
regression.

Allowed test edits are limited to call-site updates forced by relocated internal
symbols — the removed `decode_folder=` stubs in the fuzz/atheris harnesses and the
former `_METHOD_*` imports. The supported coder matrix, safety bounds, error
typing, and public API remain exactly as specified by `format-7z`,
`compressed-streams`, and `packaging-and-extras`; this change adds no requirement
to those capabilities.

#### Scenario: 7z refactor preservation matrix

| Case | Expected |
| --- | --- |
| `tests/test_sevenzip_*.py` after refactor | Pass with no removed or weakened behavioral assertions |
| 7z corpus / `py7zr` / `7z` oracle checks | Still match metadata and decoded bytes (skip only when the oracle is absent) |
| Hostile-header / bound tests (next-header caps, `num_files ≤ header_size`, UTF-16 cap) | Still raise `CorruptionError` before any large allocation |
| Encrypted header + per-folder member password paths | Still decrypt with the correct password; wrong password → typed error after CRC confirm |
| LZMA2+BCJ (core), LZMA1+BCJ (`[7z]`/pybcj), AES+LZMA2, BCJ2 reject | Byte-identical outcomes and identical error types vs. pre-refactor |
| Fuzz/atheris parser harnesses migrated off `decode_folder=` | Still exercise plain and encoded headers via the two-phase entry point |
| Test edits that only follow renamed/moved internal symbols | Allowed |
| Test edits that drop edge cases or loosen CRC/oracle checks | Not allowed for this change |
