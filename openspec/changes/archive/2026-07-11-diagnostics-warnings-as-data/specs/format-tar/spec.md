# format-tar — metadata and EOF diagnostics

## ADDED Requirements

### Requirement: Invalid TAR timestamps are member diagnostic data

The existing TAR mapping remains. If `TarInfo.mtime` cannot be represented as a Python
`datetime`, the member's `modified` SHALL be `None` and the reader SHALL emit
`MEMBER_TIMESTAMP_INVALID` with member identity, field/source kind, and a JSON-safe value
representation. Under default policy the occurrence is collected/logged and MAY attach to
the member under the shared budget; under `RAISE`, listing halts with
`DiagnosticRaisedError`.

#### Scenario: invalid TAR mtime is member data

- **WHEN** a TAR member carries an out-of-range mtime
- **THEN** `modified is None`, `MEMBER_TIMESTAMP_INVALID` is counted on the reader, and its retained occurrence may attach to the member

## MODIFIED Requirements

### Requirement: Detect truncated TAR archives

After full iteration, a missing/invalid TAR end marker SHALL emit
`ARCHIVE_EOF_MARKER_MISSING` on the reader operation aggregate. It SHALL not attach to
`ArchiveInfo`, `CostReceipt`, or a member.

Its context SHALL be `ArchiveEofContext(kind="archive_eof", format="tar",
expected_marker="two_zero_blocks", expected_bytes=1024, observed_bytes=...,
observed_kind=...)` plus the best-effort archive display name. `observed_kind` SHALL be
`"absent"`, `"short"`, or `"nonzero"` as applicable; raw trailing archive bytes SHALL
not be retained.

The event first follows the common count/retention/log/callback order. Then:

- with `strict_archive_eof=False`, ordinary disposition applies (`IGNORE` continues,
  `COLLECT` continues, `RAISE` raises `DiagnosticRaisedError`);
- with `strict_archive_eof=True`, `TruncatedError` always halts and takes precedence over
  `DiagnosticRaisedError`, including when disposition is `IGNORE` or `RAISE`.

A logging-handler or callback exception propagates at its earlier ordered step.

#### Scenario: valid TAR marker has no event

- **WHEN** full iteration ends with valid null-filled end-of-archive marker blocks
- **THEN** no EOF diagnostic or error is produced

#### Scenario: missing marker collected by default

- **WHEN** a TAR pass reaches a missing EOF marker with default config
- **THEN** `ARCHIVE_EOF_MARKER_MISSING` is counted/retained/logged on the reader and the pass completes

#### Scenario: ignored missing marker is still strict

- **WHEN** the code resolves to `IGNORE` and `strict_archive_eof=True`
- **THEN** the event count increments without delivery and `TruncatedError` is raised

#### Scenario: strict error type wins over diagnostic raise

- **WHEN** the code resolves to `RAISE`, delivery succeeds, and `strict_archive_eof=True`
- **THEN** `TruncatedError` is raised after delivery rather than `DiagnosticRaisedError`

#### Scenario: runtime EOF event does not mutate open-time info

- **WHEN** the missing marker is discovered only after iteration
- **THEN** `reader.diagnostics` changes and frozen `ArchiveInfo` / `CostReceipt` remain unchanged
