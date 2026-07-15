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
- Add stream/codec Atheris exploration targets (at least unix-compress / `.Z`; other
  archivey-owned codecs as budget allows) with per-input timeouts where hangs are known.
- Keep extract out of Atheris; mutation harness remains complementary. Adjust the
  partitioned budget so new slices do not starve 7z/RAR headers.

## Capabilities

### New Capabilities

<!-- none -->

### Modified Capabilities

- `testing-contract`: expand the coverage-guided fuzz gate — CI must provide `unrar`
  for the RAR open slice; ZIP target deepens to member read with header/payload
  mutate-then-fixup; add stream/codec Atheris targets alongside the existing
  parser/entry-point set.

## Impact

- `.github/workflows/atheris-fuzz.yml`: install `unrar`; possibly rebalance budgets /
  add targets.
- `tests/atheris_fuzz/`: new or extended targets, ZIP CRC/header fixup for deflate and
  encrypted layouts where feasible, stream/codec seeds + timeouts.
- Specs/threat-model: document the deepened gate; no public API or runtime extras.
- Mutation harness (`tests/test_mutation_fuzz.py`) unchanged in role.
