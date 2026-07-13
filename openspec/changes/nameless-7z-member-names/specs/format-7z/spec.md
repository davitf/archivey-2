## ADDED Requirements

### Requirement: Infer presented names for nameless 7z members

When the 7z `FILES_INFO` block omits the `NAME` property (every member's stored
filename is empty), the reader SHALL infer a presented `ArchiveMember.name` from
the archive source filename before `normalize_member_name`. `raw_name` SHALL
remain empty so the missing NAME channel is preserved.

Inference (aligned with single-file compressors and with `7z`/`py7zr` listing):

| Archive source | Presented name |
| --- | --- |
| Basename ends with `.7z` (case-insensitive), optionally followed by a numeric volume suffix such as `.001` | Strip `.7z` / `.7z.NNN`; use the remaining stem |
| Other non-empty basename | Strip the final suffix (last `.…`); if that leaves nothing, use the basename |
| Anonymous stream (no archive filename) | `contents` |

Every nameless member in the same archive SHALL receive the same inferred name
(duplicate names are allowed). The reader MUST NOT invent `_1` / `(1)` suffixes
at list time; destination collisions are an extraction/`OverwritePolicy` concern.

#### Scenario: nameless-member naming matrix

| Case | Expected |
| --- | --- |
| Open `github_14.7z` (no NAME property, one file) | One `FILE` member named `github_14`; `raw_name` empty |
| Open `github_14_multi.7z` (no NAME, two files) | Two `FILE` members both named `github_14_multi`; `raw_name` empty |
| Open `archive.7z.001` with no NAME | Stem is `archive` (volume suffix stripped with `.7z`) |
| Open nameless 7z from an anonymous stream | Member name `contents` |
| Open a 7z that stores NAME normally | Stored names unchanged; no stem synthesis |
