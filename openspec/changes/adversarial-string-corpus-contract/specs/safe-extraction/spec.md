## ADDED Requirements

### Requirement: Null bytes in link targets are rejected before filesystem resolution

The system SHALL reject a SYMLINK or HARDLINK whose `link_target` contains `\x00` with
`SymlinkEscapeError` before passing that target to `Path.resolve()`, `os.symlink()`, or
`os.link()`. This universal check applies under every `ExtractionPolicy`.

#### Scenario: null byte in a symlink target

- **WHEN** a SYMLINK member has `link_target == "target\x00hidden"`
- **THEN** extraction raises `SymlinkEscapeError`
- **AND** no link is created and no raw `ValueError` or `OSError` escapes
