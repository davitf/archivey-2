## Why

Anti-items are deletion markers, not files. Today's `FILE` + `is_anti: bool` makes
`if m.is_file` callers treat them as payload; `OTHER` is wrong because extraction
rejects it. Separately, `open()`/`read()` on non-files is inconsistent (empty bytes
vs raw `IsADirectoryError` vs ISO `CorruptionError`).

## What Changes

- Add **`MemberType.ANTI`**; **`is_anti`** becomes a derived property (**BREAKING** vs
  the post-#66 `is_anti: bool` field).
- Keep **`is_current`** as a field (restore/keep in data-model specs; already in code).
- **`open()`/`read()` raise `ArchiveyUsageError`** for resolved non-`FILE` members
  (**BREAKING** empty-bytes directory/anti opens).
- `stream_members` keeps yielding `None` for non-files.
- 7z anti entries use `type=ANTI`; anti extraction stays delete-only-if-written / no-op
  (restore compact safe-extraction coverage lost in the library-schema compaction).

## Capabilities

### New Capabilities

### Modified Capabilities

- `archive-data-model`: `MemberType.ANTI`; `is_anti` property; `is_current` field
- `archive-reading`: non-file `open`/`read` raise; non-file `stream_members` → `None`
- `error-handling`: non-file open listed under `ArchiveyUsageError`
- `format-7z`: anti-items are `ANTI`, not empty `FILE`
- `safe-extraction`: non-current skip; anti extract; `ANTI` ≠ special-file reject
- `testing-contract`: cross-format non-file open + 7z ANTI assertions

## Impact

- `types.py`, `base_reader` open gate, backends that synthesize empty dir streams,
  7z anti classification, extraction filters/tests.
- Specs: also re-pin `is_anti`/`is_current`/anti-extract that the compaction thinned out.
