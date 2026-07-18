## ADDED Requirements

### Requirement: Document TAR EOF honesty and the strict_archive_eof opt-in

End-user documentation SHALL state that:

- Stdlib-backed TAR may silently shorten a listing when a member header after the
  first is corrupt; archivey’s backstop is `ARCHIVE_EOF_MARKER_MISSING`.
- **By default (`ArchiveyConfig.strict_archive_eof=False`)** archivey raises
  `CorruptionError` when the stopped scan lands on a rejected (non-null) header block —
  which a well-formed tar never produces. A merely trailer-less or `cat`-joined tar
  (ended cleanly on a member boundary) is warned about, not raised, because it is
  indistinguishable from a tar truncated at a member boundary.
- `strict_archive_eof=True` narrows to one added job: escalate that ambiguous
  boundary/missing-trailer residual to `TruncatedError` too, for inventory / dedupe /
  validators that need a provably complete listing. Docs SHALL NOT describe the flag as
  "the only way archivey catches corruption" — a rejected header is caught by default.
- **Streaming limitation:** a corrupt header as the archive's *final* block is caught in
  random-access reads but NOT in forward-only streaming (it surfaces as a missing-trailer
  warning there). Docs SHALL state this and that a future native TAR reader may close the
  gap (post-v1), without promising a release.

This SHALL appear in the formats guide and in any user-facing Gotchas page once
that page exists. Internal threat-model / open-issues material MUST NOT be the only
place this is written.

#### Scenario: TAR EOF documentation matrix

| Case | Expected |
| --- | --- |
| Reader opens formats / Gotchas for TAR quirks | Finds silent-shorten + diagnostic + the rejected-header-raises-by-default vs missing-trailer-warns split |
| Inventory / dedupe guidance | Shows `ArchiveyConfig(strict_archive_eof=True)` as the escalation for the ambiguous residual, not as the only corruption backstop |
| Streaming final-header limitation | Documented as caught in random access, missed in streaming; native TAR may close it later |
| Post-v1 native TAR | Mentioned as possible future improvement for the residual + streaming gap, not a v1 promise |

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
| TAR silent shorten | Documented with diagnostic; `nonzero` raises by default, ambiguous `absent`/`short` residual warns unless strict; optional native TAR note |
| Contributor-only open-issues list | Not required reading for end users |
