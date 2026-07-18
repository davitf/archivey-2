## ADDED Requirements

### Requirement: Document TAR EOF honesty and the strict_archive_eof opt-in

End-user documentation SHALL state that:

- Stdlib-backed TAR may silently shorten a listing when a member header after the
  first is corrupt; archivey’s backstop is `ARCHIVE_EOF_MARKER_MISSING`.
- The library default is `ArchiveyConfig.strict_archive_eof=False` (warn / collect);
  inventory and dedupe sweeps that need a provably complete listing SHALL be shown
  the opt-in `strict_archive_eof=True` (escalates to `TruncatedError`).
- A future native TAR reader may make mid-archive corruption archivey’s own
  decision (post-v1); until then the docs MAY say the limitation “may improve later”
  without promising a release.

This SHALL appear in the formats guide and in any user-facing Gotchas page once
that page exists. Internal threat-model / open-issues material MUST NOT be the only
place this is written.

#### Scenario: TAR EOF documentation matrix

| Case | Expected |
| --- | --- |
| Reader opens formats / Gotchas for TAR quirks | Finds silent-shorten + diagnostic + `strict_archive_eof` opt-in |
| Inventory / dedupe guidance | Shows `ArchiveyConfig(strict_archive_eof=True)` (or equivalent) |
| Post-v1 native TAR | Mentioned as possible future improvement, not a v1 promise |

### Requirement: Gotchas page covers post-v1-fixable limitations as current behavior

When the end-user Gotchas page exists, it SHALL include current limitations that
are candidates for later native ZIP/TAR work — multi-volume ZIP rejection, ZIP/ISO
seek requirement (no pure pipe), UTF-8 general-purpose bit 11 unlistable archives,
and TAR mid-corrupt silent shorten — framed as **today’s behavior** with an
optional “may improve with a native ZIP/TAR reader later” note, not as open bugs
and not as a roadmap commitment.

#### Scenario: post-v1 limitation framing

| Case | Expected |
| --- | --- |
| Multi-volume ZIP | Documented as rejected today; optional “may improve later” |
| TAR silent shorten | Documented with diagnostic + strict opt-in; optional native TAR note |
| Contributor-only open-issues list | Not required reading for end users |
