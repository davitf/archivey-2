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
1. Replace all `\` with `/`. This is a deliberate cross-platform **separator** conversion, not
   a safety mechanism (extraction independently treats both separators and rejects unsafe
   paths); the verbatim bytes remain in `raw_name`.
2. Strip a leading `./` and collapse interior `/./` segments.
3. Collapse repeated `//` into a single `/`.
4. Append `/` for directory members if not already present.
5. Never produce an empty string — an empty name or a bare root becomes `"."`.

#### Scenario: backslash conversion

- **WHEN** an archive member is stored with the name bytes `b"foo\\bar\\baz.txt"`
- **THEN** `member.name == "foo/bar/baz.txt"` and `member.raw_name == b"foo\\bar\\baz.txt"`

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
