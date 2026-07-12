## ADDED Requirements

### Requirement: ArchiveMember anti-item flag

The system SHALL expose `is_anti: bool = False` on `ArchiveMember`. When `True`, the
member is a format-level deletion marker (7z ANTI), not ordinary file content. It is
the **faithful raw signal** from the container — the policy decisions it drives (skip
the superseded content, or delete on extract) live in `safe-extraction`, not on this
flag. Callers iterating the archive SHALL see anti members. Equality includes
`is_anti`.

#### Scenario: default is non-anti

- **WHEN** a member is produced for a normal file, directory, or link
- **THEN** `member.is_anti` is `False`

#### Scenario: 7z anti-item sets the flag

- **WHEN** a 7z anti-item is parsed
- **THEN** `member.is_anti` is `True`

### Requirement: ArchiveMember current-version flag

The system SHALL expose `is_current: bool = True` on `ArchiveMember`. It is `True`
when the member represents the **live final state** of its path within the archive,
and `False` when a later member supersedes it — another member that writes the same
path later in archive order (an updated/appended archive that re-adds a name), or a
later anti-item that deletes that path. `is_current` is a **derived**,
last-entry-wins-by-name computation, distinct from the raw `is_anti` bit:

- A content member with no same-path successor is `is_current=True`.
- A content member shadowed by a later same-path member or a later anti-item is
  `is_current=False`.
- An anti-item that is the last word on its path is itself `is_current=True` — the
  path's final state is "deleted" — even though it carries no content.

Backends that do not compute name shadowing leave every member at the default
`is_current=True`, preserving their existing behavior; the native 7z reader SHALL
compute `is_current` from the ANTI bitmask and same-name shadowing. Extraction
consumes this field per `safe-extraction` (non-current members are skipped by
default). Equality includes `is_current`.

#### Scenario: default is current

- **WHEN** a member has no later member for the same path
- **THEN** `member.is_current` is `True`

#### Scenario: shadowed duplicate is not current

- **WHEN** two members share a path and one appears later in archive order
- **THEN** the earlier member is `is_current=False` and the later member is `is_current=True`

#### Scenario: content superseded by a later anti-item

- **WHEN** a content member's path is deleted by a later anti-item
- **THEN** the content member is `is_current=False`, and the anti-item is `is_anti=True` and `is_current=True`
