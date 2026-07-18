## Why

VISION’s “hashes without decompression” path is incomplete for single-file
compressors: docs/specs claim zlib has no cheap stored digest (false — RFC 1950
Adler-32 trailer), and multi-member lzip already walks every trailer CRC+size in
its index but discards the CRCs instead of combining them into one whole-member
digest. Filling those gaps is additive parity work, separate from the
api-coherence **type** cleanup (`HashAlgorithm` + `bytes` values).

## What Changes

- Surface standalone **zlib** Adler-32 as `member.hashes[HashAlgorithm.ADLER32]`
  when cheaply peekable (seekable/path, single complete stream).
- Surface **lzip** whole-member `CRC32` for **multi-member** streams by combining
  per-trailer CRCs with known uncompressed lengths (`crc32_combine`), not only
  the single-member case.
- Teach the shared verify hasher table to compute/compare `adler32` (stdlib
  `zlib.adler32`) so surfaced values stay consistent with read-path checks.
- Update `format-single-file-compressors` / formats docs / corpus assertions for
  the new matrix rows.
- **Out of scope here:** `HashAlgorithm` enum introduction and crc32 `int`→`bytes`
  migration (api-coherence Q6 review fix). gzip/xz multi-unit combine (hard to
  acquire mid-unit trailers / CRC64 default) — deferred; single-unit-only stays.

## Capabilities

### New Capabilities

<!-- none -->

### Modified Capabilities

- `format-single-file-compressors` — stored-digest surfacing matrix (zlib Adler-32;
  lzip multi-member combined CRC32)
- `compressed-streams` — verify path recognizes `adler32` as a computable digest
- `testing-contract` — cross-format stored-digest expectations for zlib / multi-lzip
- `documentation` — `docs/formats.md` stored-digests table (zlib / lzip rows)

## Impact

- Modules: `codecs.py` (zlib/lzip `extract_metadata`), `single_file_reader.py`
  (probes), `lzip.py` (retain trailer CRCs in index), new small
  `crc32_combine`/`adler32_combine` helpers (3.11 has no stdlib combine),
  `verify.py` hasher registry.
- Public API: additive `hashes` keys/values only (after `HashAlgorithm` lands);
  no signature breaks in this change.
- Extras/deps: none (stdlib `zlib`).
- Tests: unit tests for combine helpers; single-file metadata tests for zlib
  Adler-32 and multi-member lzip; update formats matrix / sweep expectations.
- **Prerequisite:** api-coherence Q6 hashes typing (`Mapping[HashAlgorithm, bytes]`)
  merged or stacked first so this change writes enum keys, not stringly `"crc32"`.
