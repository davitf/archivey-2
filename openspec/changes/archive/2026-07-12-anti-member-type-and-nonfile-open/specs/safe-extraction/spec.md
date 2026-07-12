## ADDED Requirements

### Requirement: Skip non-current members by default

`extract` / `extract_all` SHALL skip members with `is_current is False` by default
(`ExtractionStatus.SKIPPED`; no write; no bomb-limit counting for the skip). This
is **hardwired coordinator behavior**, not the policy `filter` / `MemberFilter`
pipeline: the skip happens after the optional user `filter` runs so callers can
inspect or rewrite non-current members, then the coordinator still skips writing
them unless a future explicit opt-in lands.

How surfaces interact:

| Surface | Non-current members |
| --- | --- |
| `members()` / `__iter__` / `get` | Visible (metadata + `is_current=False`) |
| `members=` selector | May select them; they still participate in the extract walk |
| User `filter` (`MemberFilter`) | **Invoked** on them (same as current members) |
| Default extract write | Skipped after filter; `SKIPPED` result |
| `open`/`read` on superseded `FILE` | Still allowed (payload exists); not gated by `is_current` |

There is no extract-all flag in this change to force writing non-current
revisions; callers that need those bytes use `open`/`read` (or a future opt-in).

#### Scenario: non-current skip matrix

| Case | Expected |
| --- | --- |
| Content superseded by later same-name or anti | `SKIPPED` on extract; path absent on fresh dest |
| User `filter` receives non-current member | Filter is called; returning the member does not force a write |
| `open` superseded content `FILE` | Bytes returned (random access still works) |

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
