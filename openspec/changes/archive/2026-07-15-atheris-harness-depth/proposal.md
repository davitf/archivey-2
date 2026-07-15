## Why

The Atheris gate finds real bugs (7z header bombs, `.Z` CLEAR seek collisions), but
several high-value paths are under-exercised: the RAR open slice was silently skipped
in CI (no `unrar`), ZIP member **read** now goes through archivey's codec/AES streams
while the harness still only lists, and stream/codec hostile inputs are only hit
accidentally via `detect_format`. Mutation fuzz covers extract but is not
coverage-guided; Atheris is the right tool for deep read/stream exploration.

## What Changes

- Ensure the Atheris CI job installs RARLAB `unrar` so the RAR open+list target runs
  (not only `rar_header`).
- Deepen the ZIP Atheris target: mutate local/CD member headers **and** compressed
  payloads, apply broader CRC/header fixup, and exercise bounded member `open`+`read`
  through the native codec / WinZip AES paths (not list-only).
- Add **first-class stream/codec Atheris targets for all archivey-owned standalone
  codecs** (unix-compress, xz, lzip, gzip, bzip2, lzma-alone, zlib, plus optional
  extras when installed), with per-input timeouts where hang classes are known —
  not deferred to leftover budget.
- Keep extract out of Atheris; mutation harness remains complementary. Grow the
  partitioned wall budget as needed; thoroughness beats a fixed short ceiling.

## Capabilities

### New Capabilities

<!-- none -->

### Modified Capabilities

- `testing-contract`: expand the coverage-guided fuzz gate — CI must provide `unrar`
  for the RAR open slice; ZIP target deepens to member read with header/payload
  mutate-then-fixup; require stream/codec Atheris targets for archivey-owned
  standalone codecs alongside the existing parser/entry-point set.

## Impact

- `.github/workflows/atheris-fuzz.yml`: install `unrar`; longer partitioned budgets /
  more targets; job timeout may need a modest bump.
- `tests/atheris_fuzz/`: new stream targets per codec, ZIP CRC/header fixup for
  deflate and encrypted layouts where feasible, seeds + timeouts.
- Specs/threat-model: document the deepened gate; no public API or runtime extras.
- Mutation harness (`tests/test_mutation_fuzz.py`) unchanged in role.
