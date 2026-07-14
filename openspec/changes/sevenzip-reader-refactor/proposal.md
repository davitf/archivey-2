## Why

The native 7z reader/parser (`sevenzip_parser.py` + `sevenzip_reader.py`, ~2133
lines) has three readability problems: a `decode_folder` callback threaded through
four parser functions that has exactly one real implementation, five separate
`bytes`-keyed method-id maps (two of them duplicating the BCJ ids) split across
both files, and a folder-decode flow whose coder-grouping logic is interleaved with
stream I/O. The decode path is correct and safe but hard to follow and to extend.

## What Changes

- Add a new shared module `sevenzip_coders.py` holding a single `Coder` registry
  (`dict[bytes, Coder]`) that replaces the 13 `_METHOD_*` constants, `_METHOD_ALGORITHMS`,
  `_BCJ_METHODS`, `_BCJ_PYBCJ_DECODERS`, and `_SINGLE_STAGE_CODECS`. Parser and reader
  both import from it; the reader stops importing private `_METHOD_*` names from the parser.
- Remove the injected `decode_folder` callback and the `DecodeFolder` type. Invert
  control: the parser becomes pure `bytes → structures`, and the reader drives the
  "decode encoded header → re-parse" loop (explicitly bounded, replacing the parser's
  open recursion). **BREAKING** (internal): `parse_sevenzip_archive`'s signature drops
  `decode_folder=`; the fuzz/oracle harnesses that call it are updated.
- Split `open_folder_pipeline` into a pure `plan_pipeline(folder) -> list[Stage]`
  (all coder grouping + reject/validation logic, including the load-bearing LZMA1+BCJ
  liblzma-truncation workaround staging) and a trivial `execute()` fold that opens each
  stage. No behavior change.
- Rename the colliding `_folder_unpack_size` (defined in both files with different
  meanings) so the parser's bind-pair computation and the reader's member-size sum are
  distinct names.

All current behavior, every safety guard, and every threat-model comment are preserved.

## Capabilities

### New Capabilities
<!-- None: no new library contract. -->

### Modified Capabilities
<!-- None: this is a pure implementation refactor; format-7z requirements are unchanged. -->

## Impact

- **Modules**: new `src/archivey/internal/backends/sevenzip_coders.py`; rewrites within
  `sevenzip_parser.py` and `sevenzip_reader.py`. No change to `codecs.py`/`crypto.py`.
- **Public API**: none. All touched symbols are internal (`archivey.internal.backends`).
- **Internal API**: `parse_sevenzip_archive` loses `decode_folder=`; `open_folder_pipeline`
  gains an internal plan/execute split.
- **Extras/deps**: unchanged (`[7z]` pybcj staging, `[crypto]` AES, BCJ2 rejection all intact).
- **Tests**: update `tests/fuzz_sevenzip_parser.py`, `tests/atheris_fuzz/targets.py`,
  `tests/test_atheris_crc_fixup.py` (they pass `decode_folder=`). Existing 7z oracle/reader
  tests must stay green unchanged; add unit tests for the new `Coder` registry and
  `plan_pipeline`.
