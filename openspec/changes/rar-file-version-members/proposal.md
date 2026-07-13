## Why

WinRAR `-ver` archives store prior revisions of a path as real FILE payloads
(RAR5 extra `0x04` / RAR3 `FILE_VERSION`). Archivey currently drops those rows
(matching `rarfile`), which hides recoverable content. Philosophy prefers
exposing format features as data; default extract already skips
`is_current=False`, so history can be visible without changing safe defaults.

## What Changes

- **BREAKING (listing):** RAR file-version history rows appear in `members()` /
  iteration instead of being omitted.
- Present history as members with `is_current=False` and WinRAR/`unrar`-shaped
  names (`path;n`); the live revision keeps the plain path and `is_current=True`.
- `open` / `read` of a history member SHALL return that revision’s bytes via
  `unrar p` (exact `path;n` member name; optional `-ver` only if needed for
  bulk paths).
- Default `extract` / `extract_all` SHALL skip history rows via the existing
  non-current skip (no new extract flag in this change).
- Fixture + tests for RAR5 `-ver` (and RAR3 if cheap); oracle comparisons that
  assume rarfile’s omit behavior get explicit carve-outs.

## Capabilities

### New Capabilities

<!-- none -->

### Modified Capabilities

- `format-rar`: expose file-version members; wire data reads to `unrar`
- `archive-data-model`: clarify RAR version history vs 7z last-entry-wins
  `is_current` (same flag, different provenance)
- `testing-contract`: rarfile oracle exceptions for versioned members

## Impact

- Parser/reader: `rar_parser.py`, `rar_reader.py` / `rar_unrar.py`
- Public surface: more `ArchiveMember` rows on `-ver` archives; extract
  defaults unchanged for live paths
- Specs: reverse today’s omit-in-code behavior into an expose+non-current
  contract (main `format-rar` has no omit requirement yet; implementation
  still skips)
- Tests: new `-ver` fixtures under `tests/fixtures/rar/`; adjust any
  list-equality vs rarfile
