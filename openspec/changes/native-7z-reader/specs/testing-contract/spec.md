## ADDED Requirements

### Requirement: Native 7z anti-item fixtures use the 7z CLI oracle

Because `py7zr` cannot parse archives that include the 7z `ANTI` property, tests for
anti-item listing and extract-delete behavior SHALL build fixtures with the `7z` CLI
(or committed fixtures produced by it) and validate against `7z l -slt` / `7z x`
behavior. Those tests SHALL skip (not fail) when the `7z` binary is absent. Ordinary
non-anti 7z corpus cross-validation against `py7zr` remains as already specified.

#### Scenario: anti-item extract matches 7z CLI

- **WHEN** a 7z archive containing an anti-item is extracted by archivey and by `7z x` into matching destination trees
- **THEN** the resulting trees match (anti paths removed; non-anti content identical)
- **AND** the test is skipped if `7z` is not installed

#### Scenario: py7zr oracle still used for non-anti archives

- **WHEN** a non-anti 7z corpus archive is read by the native reader and by `py7zr`
- **THEN** member metadata and decompressed bytes match per the existing native↔py7zr scenario
