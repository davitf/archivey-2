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

The diagnostic first follows normal count/retention/log/callback ordering. With
`strict_archive_eof=False`, ordinary disposition applies. With
`strict_archive_eof=True`, `TruncatedError` SHALL halt after delivery and take
precedence over `DiagnosticRaisedError`, including when disposition is `IGNORE`
or `RAISE`. Logging-handler or callback exceptions propagate at their earlier
ordered step.

The library default for `strict_archive_eof` SHALL remain `False` (Option D of
`decide-strict-archive-eof-default` — replace this sentence if Option B/C/E
wins). Stdlib `tarfile` treats a corrupt member header after the first as a clean
end of archive (no exception; iteration stops early). That silently shortened
listing almost never lands on a valid two-block null trailer, so it SHALL surface
through this same EOF diagnostic. The system SHALL NOT claim the diagnostic
distinguishes “missing trailer on a complete listing” from “corrupt mid-archive
header” until a native TAR header walker owns iteration.

#### Scenario: TAR EOF matrix

| Case | Expected |
| --- | --- |
| Valid two-block null marker | No EOF diagnostic or error |
| Missing marker, default config | `ARCHIVE_EOF_MARKER_MISSING` counted/retained/logged; pass completes |
| Code resolves to `IGNORE` and `strict_archive_eof=True` | Count increments without delivery; `TruncatedError` raises |
| Code resolves to `RAISE`, delivery succeeds, `strict_archive_eof=True` | `TruncatedError` raises after delivery instead of `DiagnosticRaisedError` |
| Marker issue discovered after iteration | `reader.diagnostics` changes; frozen `ArchiveInfo` / `CostReceipt` stay unchanged |
| Mid-archive corrupt non-first header (stdlib silent EOF), default config | Listing may be short; `ARCHIVE_EOF_MARKER_MISSING` still emitted when the trailer check fails |
| Mid-archive corrupt non-first header, `strict_archive_eof=True` | `TruncatedError` after the pass (same escalation path as a missing trailer) |
