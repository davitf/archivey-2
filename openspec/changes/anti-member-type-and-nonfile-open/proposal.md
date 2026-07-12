## Why

7z anti-items are deletion markers, not file payload. Classifying them as `MemberType.FILE`
(with an `is_anti` flag) makes naive callers treat them as regular files
(`if m.is_file`, `stream_members` data paths). `MemberType.OTHER` is wrong too: it means
device/FIFO/socket and is always rejected at extraction. Meanwhile `open()`/`read()` on
directories (and other non-files) is inconsistent across backends — ZIP/TAR/7z return
empty bytes, the directory reader raises raw `IsADirectoryError`, ISO raises
`CorruptionError` — with no library contract. Both need a uniform rule.

## What Changes

- Add **`MemberType.ANTI`**: format-level deletion / tombstone members (7z ANTI bit today;
  reusable if other formats gain the same concept).
- **`is_anti`** becomes a convenience property (`type == MemberType.ANTI`), matching
  `is_file` / `is_dir` / `is_other` — not a parallel bool field. (**BREAKING** relative to
  the in-flight `native-7z-reader` change, which planned `is_anti: bool` as a field; amend
  that change or land this after and migrate.)
- **Non-file `open()` / `read()` raise** uniformly for `DIRECTORY`, `SYMLINK`/`HARDLINK`
  when not followed to a file, `OTHER`, and `ANTI`. `stream_members()` already yields
  `None` for non-files; keep that. (**BREAKING** for callers that relied on empty-bytes
  directory/`anti` opens.)
- 7z anti-items (when the native reader lands) use `type=ANTI`, no data stream, extraction
  still via the anti/safe-extraction rules (delete-only-if-this-extraction-wrote / no-op).
- `is_current` (from `native-7z-reader`) is unchanged: still the derived last-entry-wins
  flag, orthogonal to type.

## Capabilities

### New Capabilities

- (none)

### Modified Capabilities

- `archive-data-model`: add `MemberType.ANTI`; define `is_anti` as a property; clarify
  non-file taxonomy vs `OTHER`.
- `archive-reading`: `open()`/`read()` SHALL raise for non-file members (after link
  following); `stream_members` continues to yield `None` streams for non-files.
- `error-handling`: name the error used when opening/reading a non-file member
  (`ArchiveyUsageError`).
- `format-7z`: anti-items are `MemberType.ANTI` (not `FILE` + empty payload); open/read
  raise; `stream_members` yields `None`.
- `safe-extraction`: anti extraction keys off `type == ANTI` / `is_anti` (equivalent);
  `OTHER` remains always rejected; `ANTI` is not rejected as a special file.
- `testing-contract`: cross-format tests that directory/`OTHER`/`ANTI` open/read raise and
  that `stream_members` yields `None` for them.
- `format-directory` / `format-zip` / `format-tar` / `format-iso`: directory members follow
  the non-file open/read rule (no empty-byte streams, no raw OS/`CorruptionError` leakage).

## Impact

- Public API: new `MemberType.ANTI`; `ArchiveMember.is_anti` as `@property`; **BREAKING**
  empty-bytes `open`/`read` on directories (and anti-items once present).
- Touch: `types.py`, `base_reader.open`/`read` gate, all backends' `_open_member` /
  `_iter_with_data` paths that synthesize empty streams for dirs, extraction anti branch,
  filters (`OTHER` vs `ANTI`), tests.
- Depends on / amends: `native-7z-reader` anti-item classification and its
  “opening an anti-item yields no payload” scenario (replace with raise + `ANTI` type).
- No new dependencies.
