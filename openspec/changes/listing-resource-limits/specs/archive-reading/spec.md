## ADDED Requirements

### Requirement: Listing resource limits

The system SHALL define frozen `ListingLimits` and apply them from the reader's
open `ArchiveyConfig.listing_limits` when registering members into a
materialized or resolved member list (`members()`, `scan_members()`, and any
path that materializes via `_get_members_registered` / equivalent). There is no
per-call listing-limits override.

```python
@dataclass(frozen=True)
class ListingLimits:
    max_members: int | None = 1_048_576
    max_metadata_bytes: int | None = 64 * 2**20  # 64 MiB
    UNLIMITED: ClassVar["ListingLimits"]
```

`None` on a field disables that guard. `ListingLimits.UNLIMITED` disables both.
Crossing either guard SHALL raise `ResourceLimitError` naming the knob and
limit. Format-local parser bounds (e.g. 7z header-size checks) MAY still raise
`CorruptionError` for nonsensical headers and are complementary, not a
substitute.

**Unguarded by design:** `stream_members()` / forward-only iteration MUST NOT
enforce `ListingLimits` (O(1) escape hatch). Callers that need a full resolved
list use `members()` / `scan_members()` and accept the caps.

#### Scenario: listing-limits matrix

| Case | Expected |
| --- | --- |
| Default config, archive with ≤1_048_576 members and metadata under 64 MiB | `members()` / `scan_members()` succeed |
| Registered member count would exceed `max_members` | `ResourceLimitError` before/at that registration; no full cache published |
| Cumulative retained metadata would exceed `max_metadata_bytes` | `ResourceLimitError` naming `max_metadata_bytes` |
| `ListingLimits.UNLIMITED` | Count and metadata guards disabled |
| `stream_members()` over an archive that would fail `members()` under defaults | Iteration proceeds without listing-limit errors |
| `extract_all` path that materializes members first | Same listing caps as `members()` before extraction bomb guards |

### Requirement: Listing metadata-byte accounting

The system SHALL measure `max_metadata_bytes` as the sum of retained
string/bytes field lengths accumulated as members are registered, plus
archive-level `ArchiveInfo.comment` once when known:

- `str` fields `name`, `comment`, `link_target`, `uname`, `gname`:
  `len(s.encode("utf-8", "surrogateescape"))`
- `raw_name`: `len(raw_name)` when not `None`
- `extra`: lengths of `str` / `bytes` values; for a one-level `dict` value,
  lengths of nested `str` / `bytes` values only
- Exclude: `_raw`, `hashes`, diagnostics, Python object overhead

#### Scenario: metadata accounting matrix

| Case | Expected |
| --- | --- |
| Member with long `name` + `raw_name` | Both lengths count |
| Huge `ArchiveInfo.comment` alone | Counts toward the budget once |
| `extra` holds opaque non-str/bytes object | Not counted |
| Undecodable surrogateescape name | Stable UTF-8-with-surrogateescape byte length |

## MODIFIED Requirements

### Requirement: Explicit configuration object

The system SHALL define these complete frozen schemas:

```python
@dataclass(frozen=True)
class ExtractionLimits:
    max_extracted_bytes: int | None = 2 * 2**30
    max_ratio: float | None = 1000.0
    ratio_activation_threshold: int = 5 * 2**20
    max_entries: int | None = 1_048_576
    UNLIMITED: ClassVar["ExtractionLimits"]

@dataclass(frozen=True)
class ListingLimits:
    max_members: int | None = 1_048_576
    max_metadata_bytes: int | None = 64 * 2**20
    UNLIMITED: ClassVar["ListingLimits"]

@dataclass(frozen=True)
class ArchiveyConfig:
    use_rapidgzip: AcceleratorMode = AcceleratorMode.AUTO
    use_indexed_bzip2: AcceleratorMode = AcceleratorMode.AUTO
    strict_archive_eof: bool = False
    extraction_limits: ExtractionLimits = ExtractionLimits()
    listing_limits: ListingLimits = ListingLimits()
    diagnostic_policy: DiagnosticPolicy = DiagnosticPolicy()
    max_retained_diagnostic_references: int = 256
    on_diagnostic: Callable[[Diagnostic], None] | None = None
```

`max_retained_diagnostic_references` SHALL be non-negative. Policy/default/override
mappings and the dataclasses SHALL be defensively immutable. `config=None` →
immutable library default. No mutable global/context-local diagnostic policy or
callback.

A reader carries its open config, including `listing_limits` for its lifetime.
Later `extract_all(config=...)` MAY override policy/callback/strictness/
accelerators/`extraction_limits` for new work, but SHALL NOT change the
reader's effective `listing_limits` or
`max_retained_diagnostic_references` (see `diagnostics`). Per-call `limits`
still beat `config.extraction_limits`, then reader/library default. Other
per-call operational args stay outside `ArchiveyConfig`.

`strict_archive_eof=False` follows ordinary diagnostic policy for failed EOF check;
`True` forces `TruncatedError` after ordered diagnostic rules in `error-handling`.

`on_diagnostic` runs synchronously after count/retention/logging updates. Snapshot
reads from a callback are allowed. Starting another operation on the same
emitting reader/stream SHALL raise `UnsupportedOperationError`; other readers OK.
Callbacks hold no Archivey collector/reader/stream/backend/registry lock
(`diagnostics` / `reader-concurrency`).

#### Scenario: config matrix

| Case | Expected |
| --- | --- |
| `ArchiveyConfig()` | AUTO accelerators; EOF strictness false; documented extraction and listing defaults; COLLECT; budget 256; no callback |
| Reader budget 10, then `extract_all(config=…budget=1000)` | New policy/callback may apply; diagnostics still under budget 10 |
| `extract(..., extraction_limits=ExtractionLimits(max_ratio=100))` | 100:1 per-member ratio enforced (`safe-extraction`) |
| Reader opened with `listing_limits=ListingLimits(max_members=10)` | Listing caps stay at 10 for the reader lifetime even if later `extract_all(config=...)` omits listing_limits |
