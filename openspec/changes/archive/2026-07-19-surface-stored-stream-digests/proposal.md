## Why

VISION’s “hashes without decompression” path is incomplete for multi-member lzip:
the seekable index already walks every trailer CRC+size but previously discarded the
CRCs (or surfaced only the single-member case) instead of combining them into one
whole-member digest. Filling that gap is additive parity work, separate from the
api-coherence **type** cleanup (`HashAlgorithm` + `bytes` values).

Standalone zlib’s RFC 1950 Adler-32 trailer is **not** surfaced on `member.hashes`:
the wrapper has no size fields, so a last-4-byte peek cannot reliably mean “whole
synthetic member” under concat/trailing junk. Adler-32 remains checked by the
decompressor on read; the verify hasher table still recognizes `adler32` when an
expected digest is installed explicitly.

## What Changes

- Surface **lzip** whole-member `CRC32` for **multi-member** streams by combining
  per-trailer CRCs with known uncompressed lengths (`crc32_combine`), not only
  the single-member case.
- Add pure-Python `crc32_combine` / `adler32_combine` helpers (3.11 has no stdlib
  combine); register `adler32` in the verify hasher table.
- Update `format-single-file-compressors` / formats docs / corpus assertions for
  multi-member lzip; keep zlib in the “no cheap stored digest” row with an explicit
  note that Adler is decompressor-checked only.
- **Out of scope:** zlib Adler peek on `member.hashes`; `HashAlgorithm` enum /
  crc32 `int`→`bytes` migration (api-coherence); gzip/xz multi-unit combine.

## Capabilities

### New Capabilities

<!-- none -->

### Modified Capabilities

- `format-single-file-compressors` — stored-digest surfacing matrix (lzip
  multi-member combined CRC32; zlib remains omit)
- `compressed-streams` — verify path recognizes `adler32` as a computable digest
- `testing-contract` — cross-format stored-digest expectations for multi-lzip
- `documentation` — `docs/formats.md` stored-digests table (lzip multi-member;
  zlib omit + decompressor note)

## Impact

- Modules: `codecs.py` (lzip `extract_metadata`), `single_file_reader.py`
  (lzip CRC probe), `lzip.py` (retain trailer CRCs in index),
  `crc32_combine`/`adler32_combine` helpers, `verify.py` hasher registry.
- Public API: additive `hashes` values for multi-member lzip only; no zlib
  `ADLER32` key from this change.
- Extras/deps: none (stdlib `zlib`).
- Tests: unit tests for combine helpers; multi-member lzip metadata; corpus/docs
  parity.
- **Prerequisite:** api-coherence Q6 hashes typing (`Mapping[HashAlgorithm, bytes]`)
  merged or stacked first.
