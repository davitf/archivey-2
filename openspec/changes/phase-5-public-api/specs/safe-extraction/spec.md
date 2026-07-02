# safe-extraction — Phase 5 deltas

## ADDED Requirements

### Requirement: Extraction reads limits and strictness from the configuration object

`archivey.extract()` and `ArchiveReader.extract_all()` SHALL accept
`config: ArchiveyConfig | None` (see `archive-reading`), whose `extraction_limits`
field (an `ExtractionLimits` of `max_extracted_bytes`, `max_ratio`,
`ratio_activation_threshold`, `max_entries` — defaults unchanged from the individual
requirements) supplies the decompression-bomb limits. `extract_all()` SHALL default to
the config the reader was opened with; `archivey.extract()` SHALL default to the
library default config. Per-call operational parameters (`members`, `filter`,
`policy`, `overwrite`, `on_error`, `on_progress`, `password`) remain keyword
arguments and are not part of the config object.

The returned `list[ExtractionResult]` is accumulated unconditionally in v1: a
no-tracking mode would not bound memory on its own (readers cache the member list
internally), so it is deferred until a no-member-cache reader mode exists (see the
phase-5 design document).

#### Scenario: limits taken from the config

- **WHEN** `archivey.extract(src, dest, config=ArchiveyConfig(extraction_limits=ExtractionLimits(max_extracted_bytes=10 * 2**30)))` is called
- **THEN** the cumulative byte limit enforced is 10 GiB

#### Scenario: extract_all inherits the reader's config

- **WHEN** a reader opened with a custom `ArchiveyConfig` runs `extract_all(dest)` with no `config` argument
- **THEN** the reader's config (including its extraction limits) governs the run
