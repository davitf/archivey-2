## Why

Extraction already has bomb guards (`ExtractionLimits`), but listing does not:
`members()` / `scan_members()` will materialize whatever the header claims — a
metadata bomb of millions of entries or huge names/comments — before any
extraction guard runs. That undercuts VISION's memory-safe hostile-input claim
(threat-model O1). The 7z parser's header-size bound closed one format-local
hole; the spine still needs uniform listing caps, and extract bomb failures
should share one typed limit error with listing.

## What Changes

- Add frozen `ListingLimits` (`max_members`, `max_metadata_bytes`) on
  `ArchiveyConfig.listing_limits`, applied for the reader lifetime from
  `open_archive(config=…)`. No per-call listing override in v1.
- Enforce listing caps when members are registered into a materialized /
  resolved list (`members`, `scan_members`, extract prep that materializes).
  Keep format-local early bounds (e.g. 7z header-size) as defense-in-depth.
- **`stream_members` / forward iteration stay unguarded** (O(1) escape hatch).
- Defaults: `max_members=1_048_576` (same as extract `max_entries`);
  `max_metadata_bytes=64 MiB` (retained string/bytes accounting).
- Add `ResourceLimitError(ArchiveyError)`.
- **BREAKING:** extraction bomb trips (`max_extracted_bytes`, `max_ratio`,
  `max_entries`) raise `ResourceLimitError` instead of `ExtractionError`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `archive-reading`: `ListingLimits` schema; `ArchiveyConfig.listing_limits`;
  materialization enforcement contract; metadata-byte accounting.
- `safe-extraction`: bomb-guard failures raise `ResourceLimitError`.
- `error-handling`: add `ResourceLimitError` to the public hierarchy.
- `format-7z`: document parser-local count/header bounds as complementary to
  spine listing limits (no silent reliance on extraction `max_entries`).

## Impact

- **Public API:** new `ListingLimits` / `ResourceLimitError` exports;
  `ArchiveyConfig.listing_limits`; extract bomb `except ExtractionError`
  callers must catch `ResourceLimitError` (or broader `ArchiveyError`).
- **Modules:** `config.py`, `exceptions.py`, `base_reader` registration /
  materialization, `extraction` / `BombTracker`, 7z parser bounds (already
  partly landed), threat-model O1 status.
- **Tests:** adversarial oversized member-count / metadata fixtures; extract
  bomb error-type updates; `stream_members` remains uncapped.
- **Deps/extras:** none.
