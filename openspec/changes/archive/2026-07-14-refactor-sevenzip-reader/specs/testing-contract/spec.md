## ADDED Requirements

### Requirement: Native 7z restructure preserves behavioral suite

A structural refactor of the native 7z parser/reader (method registry, two-phase
header parse, pipeline regrouping) SHALL NOT change caller-visible 7z behavior.
The existing native 7z automated suite is the preservation gate: tests under
`tests/test_sevenzip_*.py`, 7z entries in the declarative corpus / oracle
cross-checks, password/encryption coverage, and codec-chain coverage MUST remain
green without weakening assertions or deleting cases to paper over regressions.

Allowed test edits are limited to call-site updates forced by relocated private
helpers (e.g. former `decode_folder=` stubs, `_METHOD_*` imports). Public API,
supported coder matrix, safety bounds, and error typing remain as specified by
`format-7z` / `compressed-streams` / `packaging-and-extras`.

#### Scenario: 7z refactor preservation matrix

| Case | Expected |
| --- | --- |
| `tests/test_sevenzip_*.py` after refactor | Pass without removed/weakened behavioral asserts |
| 7z corpus / `py7zr`/`7z` oracle checks | Still match metadata and bytes (skip only if oracle absent) |
| Hostile-header / bound tests (size caps, `num_files`) | Still raise `CorruptionError` before giant allocation |
| Encrypted header + encrypted member password paths | Still decrypt with correct password; wrong password → typed error after CRC fail |
| LZMA2+BCJ, LZMA1+BCJ (`[7z]`), AES+LZMA2, BCJ2 reject | Same outcomes as pre-refactor |
| Test updates that only follow renamed/moved private symbols | Allowed |
| Test updates that drop edge cases or loosen CRC/oracle checks | Not allowed for this change |
