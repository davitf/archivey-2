# Refactor native 7z reader/parser for size and clarity

## Why

The native 7z stack (`sevenzip_parser.py` + `sevenzip_reader.py`, ~2.1k lines) is
harder to follow than the format requires: method IDs live in four parallel tables,
encoded-header decode is injected as a `decode_folder` callback with a single
production implementation, and the folder pipeline hides LZMA1/LZMA2/BCJ staging in
nested rescans. That bulk and indirection slow review and make safety invariants
easy to miss — without buying extra capability.

## What Changes

- Introduce a single **method registry** (`SevenZipMethod`: id → algorithm / kind /
  codec / lzma filter / pybcj attr) so BCJ short/long IDs and decode routing live in
  one place.
- Replace the `decode_folder: Callable` injection with a **two-phase header parse**:
  the parser returns `PlainHeader | EncodedHeader`; the reader owns decode +
  re-parse (and password prompting).
- Rewrite `open_folder_pipeline` as a **registry-driven linear walk** with explicit
  stage grouping (still special-casing LZMA2+BCJ combined vs LZMA1+BCJ via pybcj).
- Table-drive `FILES_INFO` property handlers; build complete file records in one
  materialize step (no half-empty `SevenZipFileRecord` mutated later).
- Share password-attempt + folder CRC confirmation between encrypted-header and
  encrypted-member paths; drop thin forwarding wrappers.
- **No** public API, format-support, or extras changes. Behavior and safety bounds
  stay equivalent (hostile-header caps, CRC gates, linear-chain / BCJ2 rejection,
  LZMA1+BCJ staging).

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `testing-contract`: add a behavioral-preservation gate for the native 7z
  restructure (existing 7z suite / oracle / corpus remain the contract; no
  caller-visible format requirement changes).

## Impact

- **Code:** `src/archivey/internal/backends/sevenzip_*.py` (likely split to add a
  `sevenzip_methods` module and/or `sevenzip_pipeline`); tests that import private
  `_METHOD_*` / pass `decode_folder=` stubs.
- **Public API / extras / deps:** none.
- **Tests:** existing `test_sevenzip_*.py`, corpus, password, and codec suites must
  stay green; update call sites that poke parser DI or private method constants.
- **Docs (optional):** short decision note if the new layering is worth recording
  next to ADR 0001; not required for apply.
- **Risk:** medium (touches the whole native 7z path). Mitigated by no intentional
  behavior change and the existing oracle/corpus coverage.
