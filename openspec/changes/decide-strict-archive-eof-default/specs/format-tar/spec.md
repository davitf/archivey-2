## MODIFIED Requirements

### Requirement: Detect truncated TAR archives

After full iteration, a missing or invalid TAR end marker SHALL emit
`ARCHIVE_EOF_MARKER_MISSING` on the reader operation aggregate and SHALL NOT
attach to `ArchiveInfo`, `CostReceipt`, or a member. Context SHALL be
`ArchiveEofContext(kind="archive_eof", format="tar",
expected_marker="two_zero_blocks", expected_bytes=1024, observed_bytes=...,
observed_kind=...)` plus best-effort archive display name. `observed_kind` SHALL
be `"absent"`, `"short"`, or `"nonzero"`; raw trailing bytes SHALL NOT be
retained.

The library default for `strict_archive_eof` SHALL remain `False`
(Option F of `decide-strict-archive-eof-default`). The system does not observe
*why* stdlib `tarfile` stopped iterating (a real trailer, a corrupt non-first
header treated as clean EOF, or exhausted data all look identical); `observed_kind`
is the only signal, so end-of-archive strictness SHALL be resolved from it rather
than from a single monolithic flag:

- `observed_kind="nonzero"` (a full non-null block sits where a trailer/header was
  expected, with data still present) SHALL escalate to `CorruptionError` **regardless
  of `strict_archive_eof`**, after the diagnostic's normal count/retention/log/callback
  ordering. A conformant, complete tar never reaches this case: its two-or-more null
  trailer blocks stop the marker check before `observed_kind` is set. This is the
  detectable slice of stdlib `tarfile`'s "corrupt member header after the first = clean
  end of archive" behavior — a silently shortened listing that lands on live data.
- `observed_kind="absent"` or `"short"` (the handle is at or near EOF) is the
  irreducibly ambiguous residual — a complete-but-trailer-less tar and a tar truncated
  at a member boundary are indistinguishable without a native TAR header walker
  (post-v1). With `strict_archive_eof=False` (default) this SHALL follow ordinary
  diagnostic disposition (warn). With `strict_archive_eof=True` it SHALL escalate to
  `TruncatedError` after delivery.

Escalation (either `CorruptionError` or `TruncatedError`) SHALL take precedence over
`DiagnosticRaisedError`, including when the diagnostic disposition is `IGNORE` or
`RAISE`. Logging-handler or callback exceptions propagate at their earlier ordered
step. When the archive is iterated as part of an extract, escalation SHALL raise
**after** every salvageable member has been written (raise-at-end), matching the
`members()` / iteration failure mode; the extract SHALL NOT be aborted before that
point and SHALL NOT record the archive-level EOF only on a report field.

The system SHALL NOT claim the diagnostic distinguishes "missing trailer on a complete
listing" from "corrupt mid-archive header" for the `absent`/`short` cases until a native
TAR header walker owns iteration.

Truncation *inside* a member's data or across a partial header block is out of scope of
this end-of-marker check: it already raises `TruncatedError` **during iteration** (stdlib
`tarfile` raises `ReadError: unexpected end of data`, translated by the backend),
independent of `strict_archive_eof`, in both random-access and streaming modes. The
`absent`/`short` residual therefore denotes only a stream that ended cleanly on a member
boundary without a valid two-block trailer — a case byte-identical between a deliberately
trailer-less complete tar and a tar truncated exactly after a whole member, and thus not
decidable from the archive alone.

#### Scenario: TAR EOF matrix

| Case | `observed_kind` | Default (`False`) | `strict_archive_eof=True` |
| --- | --- | --- | --- |
| Valid two-block null marker (incl. minimal `tar -b1`, trailing record padding) | — (check returns OK) | No EOF diagnostic or error | No EOF diagnostic or error |
| Missing marker / truncated at member boundary | `absent` | `ARCHIVE_EOF_MARKER_MISSING` counted/retained/logged; pass completes | `TruncatedError` after delivery |
| Partial trailing block | `short` | Warn as above; pass completes | `TruncatedError` after delivery |
| Mid-archive corrupt non-first header (stdlib silent EOF), data follows | `nonzero` | `CorruptionError` after delivery | `CorruptionError` after delivery |
| `nonzero` during an extract | `nonzero` | Salvageable members written, then `CorruptionError` (raise-at-end) | Salvageable members written, then `CorruptionError` |
| Diagnostic code resolves to `IGNORE`, `nonzero` | `nonzero` | Count increments without delivery; `CorruptionError` raises | Count increments without delivery; `CorruptionError` raises |
| Diagnostic code resolves to `IGNORE`, `absent`/`short`, strict | `absent`/`short` | (default: warn only) | Count increments without delivery; `TruncatedError` raises |
| Diagnostic code resolves to `RAISE`, delivery succeeds, strict, `absent`/`short` | `absent`/`short` | (default: warn only) | `TruncatedError` raises after delivery instead of `DiagnosticRaisedError` |
| Marker issue discovered after iteration | any | `reader.diagnostics` changes; frozen `ArchiveInfo` / `CostReceipt` stay unchanged | same |
