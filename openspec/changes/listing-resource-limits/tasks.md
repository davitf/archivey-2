## 1. Public types and exports

- [x] 1.1 Add frozen `ListingLimits` (`max_members`, `max_metadata_bytes`, `UNLIMITED`) and `ArchiveyConfig.listing_limits` in `config.py`.
- [x] 1.2 Add `ResourceLimitError(ArchiveyError)` in the public exception hierarchy; export from `exceptions` / package `__init__` / `core`.
- [x] 1.3 Update `archive-reading` / `error-handling` public docs and threat-model O1 to point at the new contract.

## 2. Spine listing enforcement

- [x] 2.1 Implement retained metadata-byte accounting helper (name/raw_name/comment/link_target/uname/gname/extra str|bytes + archive comment) per design.
- [x] 2.2 Enforce `ListingLimits` at member registration / materialization in `BaseArchiveReader`; fail with `ResourceLimitError` before publishing a full cache.
- [x] 2.3 Confirm `stream_members` / forward iteration never apply listing caps; confirm extract-prep materialization does.
- [x] 2.4 Ensure `extract_all(config=...)` cannot change the reader's effective `listing_limits`.

## 3. Extraction bomb error type

- [x] 3.1 Raise `ResourceLimitError` from `BombTracker` / extraction paths for cumulative bytes, per-member ratio, archive-wide ratio, live ratio, and max entries.
- [x] 3.2 Update existing extraction bomb tests and any `except ExtractionError` assumptions for limit trips.

## 4. Format-local bounds

- [x] 4.1 Keep/verify 7z `num_files` header-size bound raises `CorruptionError` (complementary to listing caps).
- [x] 4.2 Spot-check other formats for unbounded pre-alloc from header counts; add cheap local bounds only where needed (no extract-`max_entries` reuse).

## 5. Tests

- [x] 5.1 Adversarial / synthetic fixtures: over `max_members`, over `max_metadata_bytes`, huge archive comment.
- [x] 5.2 Defaults: Linux-scale member counts under default caps succeed; `ListingLimits.UNLIMITED` disables guards.
- [x] 5.3 `stream_members` proceeds on an archive that would fail `members()` under tight listing caps.
- [x] 5.4 Matched defaults: `max_members == max_entries == 1_048_576`; listing then full extract without count surprise under defaults.

## 6. Verify

- [x] 6.1 Targeted pytest for listing limits + updated extraction bomb error types; pyrefly + ty + ruff clean on touched files.
- [x] 6.2 `openspec validate --strict listing-resource-limits`
- [ ] 6.3 Three-config smoke (`[all]`, `[all-lowest]`, `[core-only]`) for the affected tests before merge.
