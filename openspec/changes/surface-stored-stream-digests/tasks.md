## 1. Prerequisites

- [x] 1.1 Confirm api-coherence Q6 hashes typing is merged (`HashAlgorithm`,
      `Mapping[HashAlgorithm, bytes]`); stack or rebase this change on it
- [x] 1.2 Add `HashAlgorithm.ADLER32` if the typing PR only shipped `CRC32` /
      `BLAKE2SP`

## 2. Combine helpers

- [x] 2.1 Implement `crc32_combine` + `adler32_combine` under
      `archivey.internal.hashing` (pure Python; 3.11-compatible)
- [x] 2.2 Unit tests: combine equals `zlib.crc32` / `zlib.adler32` of
      concatenation for empty/short/multi-chunk cases

## 3. Zlib Adler-32 (omit from `member.hashes`)

- [x] 3.1 Do **not** peek/surface zlib Adler-32 on `member.hashes` (no size
      fields → unreliable under concat/trailing junk); document omit +
      decompressor-checked Adler
- [x] 3.2 Register `adler32` in `verify.py` hasher table (`zlib.adler32`) for
      explicitly installed expectations

## 4. Lzip multi-member CRC32

- [x] 4.1 Retain per-member CRC32 in lzip backward index entries
- [x] 4.2 In `extract_metadata`, combine CRCs with `data_size` via
      `crc32_combine`; surface `HashAlgorithm.CRC32` for one- and multi-member
- [x] 4.3 Test: multi-member fixture’s surfaced CRC equals CRC of full
      decompressed concat; single-member still matches trailer

## 5. Specs, docs, sweep

- [x] 5.1 Sync main specs from this change’s deltas (`format-single-file-compressors`,
      `compressed-streams`, `testing-contract`, `documentation`)
- [x] 5.2 Update `docs/formats.md` stored-digests table (lzip multi-member
      combined `crc32`; zlib omit + decompressor note)
- [x] 5.3 Extend corpus / focused tests for multi-lzip parity rows

## 6. Verify

- [x] 6.1 Targeted tests for combine helpers, multi-lzip CRC, verify `adler32`
- [x] 6.2 `uv run --no-sync pytest` for affected tests; `ruff format` / check on
      touched paths
- [x] 6.3 `openspec validate --strict surface-stored-stream-digests`
