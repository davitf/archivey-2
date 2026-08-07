# diagnostics — six review codes delta

## MODIFIED Requirements

### Requirement: Immutable diagnostic values with stable codes and safe typed context

Every advisory event SHALL be an immutable `Diagnostic`: opaque process-local
`occurrence_id`, stable `DiagnosticCode`, `DiagnosticSeverity`, human `message`,
and code-specific frozen `DiagnosticContext`. Codes are the machine contract;
messages are not stable.

Contexts are a closed typed union of JSON-safe immutable scalar/tuple values.
Raw bytes use an explicitly named base64 field. `to_dict()` on diagnostic and
context SHALL be `json.dumps`-safe without a custom encoder.

| Code | Variant and required fields |
| --- | --- |
| `MEMBER_NAME_NORMALIZED` | `NameNormalizationContext`: `kind="name_normalization"`, `archive_name`, `member_name`, `member_id`, `raw_name_base64`, `presented_name`, `normalized_name` |
| `MEMBER_NAME_BIDI_CONTROL` | `MemberNameControlsContext`: `kind="member_name_controls"`, `archive_name`, `member_name`, `member_id`, `raw_name_base64`, `controls` |
| `FORMAT_EXTENSION_CONFLICT` | `FormatConflictContext`: `kind="format_conflict"`, `source_name`, `extension`, `extension_format`, `detected_format` |
| `EXPLICIT_FORMAT_LISTED_EMPTY` | `UnconfirmedFormatContext`: `kind="unconfirmed_format"`, `archive_name`, `format`, `chosen_by="argument"`, `detected_format` |
| `EXTENSION_FORMAT_UNCONFIRMED` | `UnconfirmedFormatContext`: `kind="unconfirmed_format"`, `archive_name`, `format`, `chosen_by="extension"`, `detected_format=None` |
| `EMPTY_ARCHIVE` | `EmptyArchiveContext`: `kind="empty_archive"`, `archive_name`, `format` |
| `ENCODING_ARGUMENT_UNUSED` | `UnusedArgumentContext`: `kind="unused_argument"`, `archive_name`, `argument="encoding"`, `format`, `reason` |
| `PASSWORD_ARGUMENT_UNUSED` | `UnusedArgumentContext`: `kind="unused_argument"`, `archive_name`, `argument="password"`, `format`, `reason` |
| `SCAN_DIRECTORY_VANISHED` | `ScanRaceContext`: `kind="scan_race"`, `archive_name`, `relative_path`, `entry_kind="directory"` |
| `SCAN_ENTRY_VANISHED` | `ScanRaceContext`: `kind="scan_race"`, `archive_name`, `relative_path`, `entry_kind="entry"` |
| `ARCHIVE_EOF_MARKER_MISSING` | `ArchiveEofContext`: `kind="archive_eof"`, `archive_name`, `format`, `expected_marker`, `expected_bytes`, `observed_bytes`, `observed_kind` |
| `MEMBER_TIMESTAMP_INVALID` | `MemberTimestampContext`: `kind="member_timestamp"`, `archive_name`, `member_name`, `member_id`, `field`, `source`, `value_repr` |
| `SYMLINK_TARGET_UNAVAILABLE` | `SymlinkTargetContext`: `kind="symlink_target"`, `archive_name`, `member_name`, `member_id`, `reason` |
| `DIGEST_UNVERIFIABLE` | `DigestContext`: `kind="digest"`, `archive_name`, `member_name`, `member_id`, `algorithm`, `reason` |
| `SEEK_INDEX_DEGRADED` | `SeekIndexContext`: `kind="seek_index"`, `archive_name`, `member_name`, `member_id`, `codec`, `scan`, `error_type` |
| `STREAM_REWIND_REDECOMPRESSES` | `StreamRewindContext`: `kind="stream_rewind"`, `archive_name`, `member_name`, `member_id`, `codec`, `from_offset`, `to_offset`, `accelerator` |
| `EXTRACTION_MEMBER_BLOCKED` | `ExtractionOutcomeContext`: `kind="extraction_outcome"`, `…`, `status="blocked"`, `error_type`, `failure_group_id`, `failure_group_size` |
| `EXTRACTION_MEMBER_FAILED` | `ExtractionOutcomeContext`: `kind="extraction_outcome"`, `…`, `status="failed"`, `error_type`, `failure_group_id`, `failure_group_size` |

(`str | None` / `int | None` as in the typed variants.) `DiagnosticContext` is
exactly this union — no backend-defined variants. `observed_kind` ∈
`{"absent","short","nonzero"}`. `expected_marker` is symbolic (e.g.
`"two_zero_blocks"`). `member_id` MAY be `None` only before registration.
`failure_group_id`/`failure_group_size` both set only when multiple hardlink
results share one failed source; else both `None`. `EXTRACTION_MEMBER_BLOCKED`
pairs with `ExtractionStatus.BLOCKED`; the two share the `"blocked"` vocabulary.
`controls` SHALL be the comma-joined `U+XXXX` spellings of the bidi codepoints
found, in the order they occur, so a caller can tell an override from a mark
without re-scanning the name. `chosen_by` ∈ `{"argument","extension"}`;
`detected_format` is `None` when detection refuses the bytes outright.

No diagnostic surface SHALL contain passwords, candidates, provider returns, keys,
KDF material, or decrypted secrets. `PASSWORD_ARGUMENT_UNUSED` therefore records
that a password argument was supplied and unused — never how many candidates, and
never any candidate value.

Copies on multiple surfaces MAY share `occurrence_id` by value; object identity
and cross-run id stability are not promised.

#### Scenario: value-model matrix

| Case | Expected |
| --- | --- |
| Name normalization | `MEMBER_NAME_NORMALIZED` + typed JSON-safe context; no backend/mutable mapping |
| Same occurrence on aggregate + member | Same `occurrence_id`; value equality; no object-identity promise |
| Encrypted symlink unavailable | May use reason `"password_required"` + member name; no secret material |
| Member blocked by a universal/policy check | `EXTRACTION_MEMBER_BLOCKED` with `status="blocked"`; pairs with a `BLOCKED` result |
| `password=["a","b"]` on a format with no encryption | `PASSWORD_ARGUMENT_UNUSED`; context carries no candidate value and no count |

### Requirement: Complete initial warning taxonomy

The initial `DiagnosticCode` set SHALL cover the library's advisory emissions via
the codes in the closed table above. Multiple call sites share a code only with the
same machine meaning; typed context distinguishes variants.

**No advisory SHALL be log-only.** Every condition the library reports as advice to
the caller SHALL be emitted through the central diagnostic path with a code, so it is
queryable on `reader.diagnostics` and escalatable by `DiagnosticPolicy`; the WARNING
log line is the projection of that emission, never a substitute for it.

Extraction count unit = one continued result with matching status. Under
`IGNORE`/`COLLECT`, one failed hardlink source causing `N` failed link results →
`N` `EXTRACTION_MEMBER_FAILED` occurrences with shared failure-group fields. Under
`RAISE`, the first ordered occurrence escalates (no completed-result count
guarantee). No unused future codes reserved.

#### Scenario: taxonomy coverage

| Case | Expected |
| --- | --- |
| Advisory path that formerly logged only | Emits one of the initial codes through the central path |
| Member name containing a bidi formatting control | `MEMBER_NAME_BIDI_CONTROL` on the aggregate, not only a `logger.warning` |

## ADDED Requirements

### Requirement: Report unused explicit arguments as diagnostics

When an explicit argument is a **resource offered for use if needed** rather than an
assertion about the archive (see `archive-reading`), `open_archive()` SHALL accept it
and, when the resolved backend cannot act on it, SHALL emit one diagnostic naming the
argument. It MUST NOT raise.

| Condition | Code | `reason` |
| --- | --- | --- |
| Caller passed `encoding=` and `ReadBackend.USES_ENCODING` is `False` | `ENCODING_ARGUMENT_UNUSED` | why the backend decodes names another way |
| Caller passed any `password=` form and `ReadBackend.SUPPORTS_PASSWORD` is `False` | `PASSWORD_ARGUMENT_UNUSED` | that the format carries no encryption |

Each SHALL be emitted **at most once per `open_archive()` call**, before the reader is
returned, so a caller can inspect `reader.diagnostics` without listing anything.

An `encoding` value that came from detection's `encoding_hint` rather than from the
caller SHALL NOT emit — the caller asked for nothing.

`password=` SHALL behave identically in all three forms (a single value, a sequence of
candidates, a provider callable) on a format with no encryption: accepted, never
consulted, one diagnostic. A wrong password on an *encrypted* archive is unaffected and
still raises.

#### Scenario: unused argument matrix

| Case | Expected |
| --- | --- |
| `open_archive(iso, encoding="cp500")` | Opens; one `ENCODING_ARGUMENT_UNUSED`; names unchanged |
| `open_archive(zip, encoding="cp500")` | No diagnostic; the encoding is applied |
| Auto-detected encoding hint on a backend that ignores encoding | No diagnostic |
| `open_archive(tar, password="p")` / `password=["a","b"]` / `password=lambda r: "p"` | All three open; one `PASSWORD_ARGUMENT_UNUSED` each; no `UnsupportedOperationError` |
| Wrong password on an encrypted ZIP | Unchanged: `EncryptionError` |

### Requirement: Report an empty listing as a diagnostic

When a member listing completes **without error** and contains **zero members**, the
system SHALL emit `EMPTY_ARCHIVE` exactly once per reader, whatever the format. It MUST
NOT raise: a legitimately empty tar is 10240 zero bytes, byte-identical to a zero-filled
garbage file of the same length, so no predicate over the bytes can separate them and any
"zero members is an error" rule would reject a file `tar(1)` itself produces.

An incomplete listing (one published with an error) SHALL NOT emit it — the member count
is not the archive's.

On an empty listing the system SHALL additionally report **how the format was chosen**,
when that choice was not confirmed against the bytes:

| How the format was chosen | Additional code |
| --- | --- |
| Explicit `format=` argument, and detection now reports a different format or refuses the bytes | `EXPLICIT_FORMAT_LISTED_EMPTY` |
| Extension fallback (`FormatInfo.detected_by == "extension"`), i.e. no magic, probe or far-magic match | `EXTENSION_FORMAT_UNCONFIRMED` |
| Magic, content probe, far magic, or a directory path | none — the bytes confirmed the format |

`format=` SHALL remain an override: neither code refuses the open. The
`EXPLICIT_FORMAT_LISTED_EMPTY` re-detection SHALL run **only** on an empty listing, and
only when the source is a filesystem path, where reopening it cannot disturb the reader;
for a stream source the check is skipped rather than reaching into a live source's
position.

#### Scenario: empty listing matrix

| Case | Expected |
| --- | --- |
| `tar cf empty.tar --files-from /dev/null` | Opens, 0 members, `EMPTY_ARCHIVE`, no error |
| 32 KiB of zeros named `z.tar` | Opens, 0 members, `EMPTY_ARCHIVE` **and** `EXTENSION_FORMAT_UNCONFIRMED` |
| `open_archive(iso_path, format=TAR)` | Opens, 0 members, `EMPTY_ARCHIVE` **and** `EXPLICIT_FORMAT_LISTED_EMPTY` with `detected_format="ISO"` |
| `open_archive(tar_path, format=TAR)` on a real one-member tar | No diagnostic |
| Empty ZIP / empty 7z | `EMPTY_ARCHIVE`; no format code (magic confirmed the bytes) |
| A legitimately empty tar (all zeros, so no magic) | `EMPTY_ARCHIVE` **and** `EXTENSION_FORMAT_UNCONFIRMED` — truthful: the bytes really did not confirm it |
| Listing published with an error and zero members | No `EMPTY_ARCHIVE` |
