## ADDED Requirements

### Requirement: Anti-item extraction deletes the destination

When extracting a member with `is_anti=True`, the system SHALL **not** write file
content. Instead it SHALL delete the destination path if it already exists inside
the extract root — matching `7z` CLI extract behavior for `Anti = +` entries —
after the same universal path-safety checks that apply to ordinary members. If the
destination does not exist, extraction of that member succeeds as a no-op. Deletion
MUST refuse paths that escape the extract root. Overwrite-policy interaction: an
anti-item targets removal, not creation; policies that only govern writing new files
do not block the delete of an existing in-root path.

#### Scenario: anti-item removes an existing file

- **WHEN** extract runs on an anti-item whose destination path exists inside the extract root
- **THEN** that path is removed and no file content is written for the member

#### Scenario: anti-item with missing destination

- **WHEN** extract runs on an anti-item whose destination does not exist
- **THEN** the member extracts successfully without error and without creating the path

#### Scenario: anti-item cannot escape the extract root

- **WHEN** an anti-item name would resolve outside the extract root
- **THEN** extraction is rejected by the universal path-safety checks (no delete outside the root)
