## ADDED Requirements

### Requirement: Non-current members are skipped by default

When a member has `is_current=False` (superseded by a later same-path member or by a
later anti-item — see `archive-data-model`), extraction SHALL skip it by default: no
content is written, and the member does NOT count toward the entry-count, cumulative-
byte, or ratio guards. The member receives a `SKIPPED` `ExtractionResult`.

This makes the extracted tree the archive's **final state** (last write wins; deleted
paths absent) rather than a replay of every superseded revision, and it keeps an
archive's own internal duplicate names from tripping `OverwritePolicy.ERROR`. A caller
MAY opt in to materializing superseded revisions through a future explicit option; the
default extracts only current members.

#### Scenario: superseded duplicate is skipped

- **WHEN** an archive holds two members for the same path and it is extracted with default options
- **THEN** only the current (last) member is written; the superseded member gets a `SKIPPED` result and is not written

#### Scenario: non-current member does not count against limits

- **WHEN** a non-current member is skipped
- **THEN** it does not increment the entry count, the cumulative extracted-byte total, or the ratio denominators

### Requirement: Anti-item extraction never deletes data it did not create

When extracting a member with `is_anti=True`, the system SHALL NOT write file content,
and SHALL NOT delete any on-disk entry that this same extraction did not create.
Because the content member an anti-item supersedes is already skipped as non-current
(above), an anti-item is by default a **no-op on disk**: the deleted path is simply
never written in the first place.

Only when — within the same extraction — an earlier member wrote the anti-item's
destination path and the anti-item then supersedes it, the system SHALL delete that
just-created entry, using `lstat`/`unlink` semantics (the directory entry itself is
removed, never followed through a symlink), and only when it is a file or an **empty**
directory — never a recursive delete of a populated directory. The universal
path-safety checks apply as for any member: a delete MUST refuse a path that escapes
the extract root, and MUST NOT touch a destination the extraction did not write.

Applying an anti-item as a deletion against a **pre-existing** destination tree (the
`7z x` "differential restore" behavior, which removes files already on disk from an
earlier extraction of the base) is NOT the default. It is available only through an
explicit opt-in extraction mode; the safe default never removes data the current
extraction did not produce.

#### Scenario: anti-item over a pre-existing destination is a no-op

- **WHEN** an anti-item's destination already exists on disk but was not written by this extraction
- **THEN** the existing entry is left untouched, no content is written, and the member succeeds

#### Scenario: anti-item with a missing destination

- **WHEN** an anti-item's destination does not exist
- **THEN** the member succeeds as a no-op without creating the path

#### Scenario: anti-item removes only a path created in the same extraction

- **WHEN** an earlier member of the same extraction created the anti-item's destination and the anti-item supersedes it
- **THEN** that just-created file or empty directory is removed via `lstat`/`unlink` semantics, and no pre-existing, populated-directory, or out-of-root path is deleted

#### Scenario: anti-item cannot escape the extract root

- **WHEN** an anti-item name would resolve outside the extract root
- **THEN** extraction is rejected by the universal path-safety checks (no delete outside the root)
