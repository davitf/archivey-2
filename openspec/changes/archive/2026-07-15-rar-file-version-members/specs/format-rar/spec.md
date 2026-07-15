## ADDED Requirements

### Requirement: Expose RAR file-version history members

The system SHALL include RAR file-version history FILE blocks in the member list
instead of omitting them. RAR5 extra type `0x04` (and RAR3 `FILE_VERSION` when
present) identifies a prior revision. History members SHALL use the WinRAR /
`unrar` presented name `path;n` (version `n != 0`), set
`extra["rar.file_version"] = n`, and set `is_current=False`. The live revision of
the same archive path (no version extra, or version 0) SHALL keep the plain path
name and `is_current=True`.

`open` / `read` of a history `FILE` SHALL return that revision’s bytes. For
`unrar`-backed reads the backend SHALL request the exact presented member name
(`path;n`). Solid ALL-pipe demux SHALL pass `unrar`’s `-ver` switch when the
member list contains any versioned payload FILE so the pipe includes history
bytes in archive order; otherwise solid demux MAY omit `-ver`.

Default `extract` / `extract_all` SHALL skip history rows through the existing
`is_current=False` coordinator behavior (`safe-extraction`). History rows SHALL
count toward listing / parser member ceilings like any other FILE.

#### Scenario: file-version matrix

| Case | Expected |
| --- | --- |
| RAR5 `-ver` archive with revisions 1..k then live path | Members include `path;1`…`path;k` (`is_current=False`) and `path` (`is_current=True`) |
| `read("path;1")` / `open` that member | Bytes of revision 1 |
| `read("path")` | Bytes of the live revision |
| `extract_all` default | Writes live `path` only; history rows `SKIPPED` |
| Solid archive that includes versioned payload FILEs | ALL-pipe demux uses `-ver`; stream order stays aligned |
| Nonsolid named `unrar p` of `path;n` | Exact member name; `-ver` not required |
| Hostile archive with many version rows | Rows count toward member caps |
