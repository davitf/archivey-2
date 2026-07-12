## ADDED Requirements

### Requirement: Anti-items are not special-file rejections

`MemberType.ANTI` SHALL NOT be rejected by the universal filter as a special file.
Only `MemberType.OTHER` (device nodes, FIFOs, sockets) SHALL raise `SpecialFileError`.
Anti-item extraction (no content write; delete only a path this extraction wrote)
remains defined by the anti-item extraction requirements (see `native-7z-reader` /
`safe-extraction` anti scenarios).

#### Scenario: ANTI passes the special-file check

- **WHEN** `check_universal` runs on a member with `type == MemberType.ANTI`
- **THEN** it does not raise `SpecialFileError` for that reason alone

#### Scenario: OTHER still rejected

- **WHEN** a member's type is `MemberType.OTHER`
- **THEN** `SpecialFileError` is raised regardless of `ExtractionPolicy`
