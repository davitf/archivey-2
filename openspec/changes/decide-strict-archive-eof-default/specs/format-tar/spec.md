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
(Option F of `decide-strict-archive-eof-default`). Stdlib `tarfile` does not report
*why* it stopped iterating (a real trailer, a corrupt non-first header treated as
clean EOF, or exhausted data all return the same result), so the backend SHALL
classify the end-of-archive from the block tarfile stopped on rather than from a
single monolithic flag:

- **Rejected header → `CorruptionError`, regardless of `strict_archive_eof`.** When a
  full non-null 512-byte block sits where the next header / end marker was expected,
  tarfile rejected it as a header — the detectable slice of "corrupt member header
  after the first = clean end of archive," a silently shortened listing. A conformant,
  complete tar never produces this (its two-or-more null trailer blocks end the scan
  first). Emitted with `observed_kind="nonzero"` after the diagnostic's normal
  count/retention/log/callback ordering, then escalated to `CorruptionError`.
  - In **random-access** mode the backend SHALL detect this via a read/offset probe
    (`_EofProbeStream`): it captures the block tarfile read at the last member's
    block-aligned end (`offset_data + roundup(size)`) during the scan and treats a
    full non-null block there as a rejected header. This catches the case even when the
    bad header is the archive's **final** block (nothing following). On an offset
    mismatch it SHALL fall back to the trailing-block check (no regression).
  - In **streaming** mode (no probe) the backend SHALL detect a rejected header via the
    block following tarfile's stop being full and non-null. A rejected **final** header
    (no data after it) is NOT detectable this way and surfaces as a missing trailer
    instead — see the streaming limitation below.
- **Missing / short trailer → flag-governed.** A stream that ended cleanly on a member
  boundary with no valid two-block trailer (`observed_kind="absent"` for EOF,
  `"short"` for a partial block) is the irreducibly ambiguous residual: a
  complete-but-trailer-less tar and a tar truncated exactly at a member boundary are
  byte-identical and not decidable without a native TAR header walker (post-v1). With
  `strict_archive_eof=False` (default) this SHALL follow ordinary diagnostic disposition
  (warn); with `strict_archive_eof=True` it SHALL escalate to `TruncatedError` after
  delivery.

Escalation (either `CorruptionError` or `TruncatedError`) SHALL take precedence over
`DiagnosticRaisedError`, including when the diagnostic disposition is `IGNORE` or
`RAISE`. Logging-handler or callback exceptions propagate at their earlier ordered step.

The archive-level EOF check runs at the end of the member scan, so its escalation
surfaces there. For **`extract_all`**, random access materializes the member list before
writing (extract-prep), so a corrupt/truncated archive **fails closed** — the escalation
raises before any member is written and no partial output is left on disk. Streaming
verifies at the end of the forward pass, so it writes the salvageable members first and
then raises. The check SHALL NOT record the archive-level EOF only on a report field.

Truncation *inside* a member's data or across a partial header block is out of scope of
this end-of-marker check: it already raises `TruncatedError` **during iteration** (stdlib
`tarfile` raises `ReadError: unexpected end of data`, translated by the backend),
independent of `strict_archive_eof`, in both random-access and streaming modes.

**Streaming limitation (known):** stdlib `tarfile`'s streaming `_Stream` hides its
header reads, so the random-access offset probe is unavailable and a rejected **final**
header (a corrupt header as the archive's last block, nothing following) is misclassified
as `observed_kind="absent"` — treated as a missing trailer (warn by default,
`TruncatedError` under strict) rather than `CorruptionError`. Random access catches this
case. A native TAR walker (post-v1) that validates each header at its offset would close
the gap for streaming too. The system SHALL NOT claim otherwise.

#### Scenario: TAR EOF matrix

| Case | Mode | `observed_kind` | Default (`False`) | `strict_archive_eof=True` |
| --- | --- | --- | --- | --- |
| Valid two-block null marker (incl. minimal `tar -b1`, trailing record padding) | both | — (OK) | No diagnostic or error | No diagnostic or error |
| Missing marker / truncated at member boundary | both | `absent` | `ARCHIVE_EOF_MARKER_MISSING`; pass completes | `TruncatedError` after delivery |
| Partial trailing block | both | `short` | Warn as above; pass completes | `TruncatedError` after delivery |
| Rejected non-first header, data follows | both | `nonzero` | `CorruptionError` after delivery | `CorruptionError` after delivery |
| Rejected **final** header, nothing after | random-access | `nonzero` (via probe) | `CorruptionError` after delivery | `CorruptionError` after delivery |
| Rejected **final** header, nothing after | streaming | `absent` (limitation) | Warn; pass completes | `TruncatedError` after delivery |
| Truncation inside member data / partial header | both | — | `TruncatedError` during iteration | `TruncatedError` during iteration |
| Corruption during `extract_all` | random-access | `nonzero` | Fails closed: raises before any write (no partial output) | same |
| Corruption during `extract_all` | streaming | `nonzero` | Salvageable members written, then `CorruptionError` | same |
| Diagnostic code resolves to `IGNORE`, rejected header | both | `nonzero` | Count increments without delivery; `CorruptionError` raises | same |
| Diagnostic code resolves to `IGNORE`, `absent`/`short`, strict | both | `absent`/`short` | (default: warn only) | Count increments without delivery; `TruncatedError` raises |
| Diagnostic code resolves to `RAISE`, delivery succeeds, strict, `absent`/`short` | both | `absent`/`short` | (default: warn only) | `TruncatedError` after delivery instead of `DiagnosticRaisedError` |
| Marker issue discovered after iteration | both | any | `reader.diagnostics` changes; frozen `ArchiveInfo` / `CostReceipt` unchanged | same |
