## ADDED Requirements

### Requirement: 7z anti-items are MemberType.ANTI

7z `FILES_INFO` ANTI-bit entries SHALL be exposed as `MemberType.ANTI`
(`is_anti`, not `is_file`). `open`/`read` SHALL raise `ArchiveyUsageError`;
`stream_members` SHALL yield `None`. Extraction follows `safe-extraction` anti
rules. This replaces empty-payload `FILE` anti opens.

#### Scenario: 7z anti matrix

| Case | Expected |
| --- | --- |
| ANTI-bit entry in member list | `type == MemberType.ANTI`; `is_anti`; not `is_file` |
| `open`/`read` anti member | `ArchiveyUsageError` |
| `stream_members` anti member | Stream `None` |
| Content then later anti same path | Content `is_current` false; anti `is_anti` and `is_current` true |
