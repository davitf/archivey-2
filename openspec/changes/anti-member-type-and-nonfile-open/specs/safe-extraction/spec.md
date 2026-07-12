## ADDED Requirements

### Requirement: Skip non-current members by default

Extraction SHALL skip members with `is_current is False` by default
(`ExtractionStatus.SKIPPED`; no write; no bomb-limit counting for the skip).

#### Scenario: non-current skip matrix

| Case | Expected |
| --- | --- |
| Content superseded by later same-name or anti | `SKIPPED`; path absent on fresh dest |

### Requirement: Anti-item extraction is delete-only-if-written

For `is_anti` members, extraction SHALL NOT write payload. It SHALL delete the
destination only if this same extraction wrote that path (file or empty dir via
`lstat`/`unlink`); otherwise it is a success no-op. Pre-existing, populated, or
out-of-root paths MUST NOT be deleted. `MemberType.ANTI` SHALL NOT raise
`SpecialFileError` (only `OTHER` does).

#### Scenario: anti extraction matrix

| Case | Expected |
| --- | --- |
| Anti path missing / pre-existing not written this run | Success no-op; pre-existing untouched |
| Earlier member this run wrote the path, then anti | Just-created file/empty dir removed |
| `check_universal` on `ANTI` | No `SpecialFileError` for type alone |
| `MemberType.OTHER` | Still `SpecialFileError` under all policies |
