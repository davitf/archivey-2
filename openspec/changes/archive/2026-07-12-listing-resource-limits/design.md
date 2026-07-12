## Context

Threat-model O1: extraction has `ExtractionLimits` / `BombTracker`; listing does
not. A crafted ZIP CD or 7z FilesInfo can force gigabytes of `ArchiveMember`
allocations at `members()` / `scan_members()` before any extract guard runs.
Review L1 partially fixed 7z by bounding `num_files` against header size
(`CorruptionError`); the spine still needs configurable caps, and extract bomb
failures still raise `ExtractionError` (wrong phase name for a shared safety
story).

Decisions below were locked with the maintainer in explore (2026-07).

## Goals / Non-Goals

**Goals:**

- Configurable listing caps on `ArchiveyConfig` with safe defaults.
- Uniform spine enforcement at member registration / materialization.
- Keep format-local early bounds as defense-in-depth.
- One `ResourceLimitError` for listing caps and extract bomb trips.
- Honest metadata-byte accounting (retained strings/bytes, not compressed headers).

**Non-Goals:**

- Per-call `listing_limits=` on `members()` / `scan_members()`.
- Bounding unbounded `read()` / `open()` stream sizes (follow-on).
- `__slots__` on `ArchiveMember` (optional micro-opt, separate change).
- OSS-Fuzz / Atheris (separate O5 work).
- Casefold / Windows name portability (O2–O4).

## Investigations

### Member RAM vs metadata budget

| Scale | ~`ArchiveMember` RAM (rich stand-in) | Retained name+raw (~80B paths) | TAR `N×512` headers |
| --- | --- | --- | --- |
| Linux sources (~90–100k) | ~100–110 MiB | ~15–20 MiB | ~46–51 MiB |
| Chromium-scale (~400k) | ~450 MiB | ~60–80 MiB | ~205 MiB |
| At `max_members=1M` | ~1.1 GiB | ~160 MiB | ~512 MiB |

Defaults target “no false positive on Linux sources.” A 1 GiB host is already
pressured by `max_members` object overhead long before the 64 MiB string budget;
document lowering `max_members` on constrained hosts.

### Compressed vs retained metadata

Compressed 7z header size is a weak primary guard (small packed header → huge
FilesInfo). TAR has no separate header blob. **Retained string/bytes lengths**
are format-uniform and match the string-bomb half of O1; `max_members` covers
object-count overhead.

## Decisions

### 1. Separate `ListingLimits` (do not reuse / rename `ExtractionLimits`)

Listing and extraction need **distinct knobs**. Overloading `max_entries` breaks
“list a huge archive, extract five selected members.” Rename-only to
`SafetyLimits` does not fix the phase collision; a single flat object with both
families of fields makes `extract(..., limits=)` awkward (must re-copy listing
fields or invent partial override).

**Rejected:** one shared `max_entries`; rename-only `SafetyLimits`; nested
`SafetyLimits(listing=…, extraction=…)`.

### 2. Defaults match on counts; metadata = 64 MiB retained strings

- `ListingLimits.max_members = ExtractionLimits.max_entries = 1_048_576` so an
  archive that lists under defaults can extract all members without a count
  surprise.
- `max_metadata_bytes = 64 * 2**20`.
- `None` disables a guard; `ListingLimits.UNLIMITED` disables both.

### 3. Metadata accounting

As each member is registered, add UTF-8 lengths (`surrogateescape`) of `name`,
`comment`, `link_target`, `uname`, `gname`; `len(raw_name)` when present; `str` /
`bytes` values in `extra` (one-level nesting for PAX-like dicts). Also count
`ArchiveInfo.comment` once when known. Exclude `_raw`, `hashes`, diagnostics,
and Python object overhead.

### 4. Enforcement points

- **Spine:** check before/at `_register_member` when building a materialized /
  resolved list (`members`, `scan_members`, extract paths that materialize).
- **Format-local:** keep 7z `num_files > header_size → CorruptionError` (and
  similar); that is “header is nonsense,” not “over config.”
- **`stream_members` / forward iteration:** unguarded by design (O(1) escape).
- Listing limits come only from the reader’s open `config`; no per-call override.

### 5. `ResourceLimitError` for listing and extract bombs

New `ResourceLimitError(ArchiveyError)` (sibling of `ExtractionError`, not a
subclass — limit trips are not filter/path failures). Listing and all
`ExtractionLimits` bomb trips (bytes, ratio, entries, archive-wide / live ratio)
raise it. Message names the knob and limit. **BREAKING** for callers that
caught only `ExtractionError` for bombs.

**Rejected:** keep extract on `ExtractionError` and migrate later; reuse
`ExtractionError` for listing.

### 6. Config surface

```python
@dataclass(frozen=True)
class ListingLimits:
    max_members: int | None = 1_048_576
    max_metadata_bytes: int | None = 64 * 2**20
    UNLIMITED: ClassVar[ListingLimits]

@dataclass(frozen=True)
class ArchiveyConfig:
    ...
    extraction_limits: ExtractionLimits = ...
    listing_limits: ListingLimits = ...
```

## Risks / Trade-offs

| Risk | Mitigation |
| --- | --- |
| Chromium-scale TAR false-positive on 64 MiB string budget | Unlikely under retained-string accounting; document raising the cap |
| `stream_members` escape used to bypass caps | Documented; callers who need full materialization still hit caps |
| BREAKING extract error type | Pre-1.0; changelog + update tests; `except ArchiveyError` still works |
| Double-counting name + raw_name | Intentional (both retained); still well under Linux budget |

## Open Questions

None — decisions locked for proposal.
