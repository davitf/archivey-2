# Archive Data Model — delta (minimal-name-normalization)

## MODIFIED Requirements

### Requirement: ArchiveMember name normalization rules

The system SHALL normalize `ArchiveMember.name` using only **meaning-preserving** rules,
while preserving the verbatim stored bytes in `ArchiveMember.raw_name`. Normalization SHALL
NOT perform meaning-altering rewrites — specifically it SHALL NOT strip a leading `/`
(absolute → relative) and SHALL NOT collapse `..` sequences — because those change the path's
meaning and hide an unsafe stored name. A leading `/` and any `..` component are **retained**
in `name`; such names are rejected at extraction time (see `safe-extraction`), not silently
re-rooted at read time. When normalization changes the presented path, a warning SHALL be
emitted via the `archivey.normalization` logger.

Normalization rules applied in order:
1. Replace `\` with `/` **only when the source format/entry uses backslash as a path
   separator** — a `backslash_is_separator` signal the backend supplies. Windows-origin
   entries convert (RAR; ZIP entries whose `create_system` is DOS/Windows — `FAT`,
   `WINDOWS_NTFS`, `VFAT`, `OS2_HPFS`, …); TAR and other POSIX formats keep `\` as a **literal
   filename character** (converting would corrupt a valid POSIX name). This is a separator
   convention, not a safety mechanism (extraction independently treats both separators and
   rejects unsafe paths); the verbatim bytes remain in `raw_name`.
2. Strip a leading `./` and collapse interior `/./` segments.
3. Collapse repeated `//` into a single `/`.
4. Append `/` for directory members if not already present.
5. Never produce an empty string — an empty name or a bare root becomes `"."`.

#### Scenario: backslash converted for a Windows-origin entry

- **WHEN** a Windows-origin member (RAR, or a ZIP entry with a DOS/Windows `create_system`) is
  stored with the name bytes `b"foo\\bar\\baz.txt"`
- **THEN** `member.name == "foo/bar/baz.txt"` and `member.raw_name == b"foo\\bar\\baz.txt"`

#### Scenario: backslash kept literal for a POSIX (TAR) entry

- **WHEN** a TAR member is stored with the name bytes `b"weird\\name.txt"` (backslash is a
  legal POSIX filename character)
- **THEN** `member.name == "weird\\name.txt"` (the backslash is preserved, not treated as a
  separator)

#### Scenario: internal traversal is preserved, not collapsed

- **WHEN** an archive member has the name `"foo/../bar"`
- **THEN** `member.name == "foo/../bar"` (the `..` is retained, not collapsed to `"bar"`)

#### Scenario: absolute path is preserved, not re-rooted

- **WHEN** an archive member is stored as `"/etc/passwd"`
- **THEN** `member.name == "/etc/passwd"` (the leading `/` is retained); it is rejected later
  by `safe-extraction`'s universal path check, not silently converted to `"etc/passwd"`

#### Scenario: escaping traversal is preserved

- **WHEN** an archive member is stored as `"../../etc/passwd"`
- **THEN** `member.name == "../../etc/passwd"` (retained); it is rejected at extraction time

#### Scenario: meaning-preserving cleanups still apply

- **WHEN** an archive member is stored as `"a//b/./c"`
- **THEN** `member.name == "a/b/c"`

#### Scenario: directory trailing slash

- **WHEN** a directory member is stored as `"mydir"`
- **THEN** `member.name == "mydir/"`
