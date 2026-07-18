## 1. Prerequisites

- [ ] 1.1 Confirm api-coherence Q6 hashes typing is merged (`HashAlgorithm`,
      `Mapping[HashAlgorithm, bytes]`); stack or rebase this change on it
- [ ] 1.2 Add `HashAlgorithm.ADLER32` if the typing PR only shipped `CRC32` /
      `BLAKE2SP`

## 2. Combine helpers

- [ ] 2.1 Implement `crc32_combine` + `adler32_combine` under
      `archivey.internal.hashing` (pure Python; 3.11-compatible)
- [ ] 2.2 Unit tests: combine equals `zlib.crc32` / `zlib.adler32` of
      concatenation for empty/short/multi-chunk cases

## 3. Zlib Adler-32 surfacing

- [ ] 3.1 Add seekable probe for last-4-byte Adler-32 (single-file reader /
      zlib codec `extract_metadata`)
- [ ] 3.2 Set `member.hashes[HashAlgorithm.ADLER32]` as 4-byte big-endian;
      omit on non-seekable / too-short
- [ ] 3.3 Register `adler32` in `verify.py` hasher table (`zlib.adler32`)

## 4. Lzip multi-member CRC32

- [ ] 4.1 Retain per-member CRC32 in lzip backward index entries
- [ ] 4.2 In `extract_metadata`, combine CRCs with `data_size` via
      `crc32_combine`; surface `HashAlgorithm.CRC32` for one- and multi-member
- [ ] 4.3 Test: multi-member fixture’s surfaced CRC equals CRC of full
      decompressed concat; single-member still matches trailer

## 5. Specs, docs, sweep

- [ ] 5.1 Sync main specs from this change’s deltas (`format-single-file-compressors`,
      `compressed-streams`, `testing-contract`, `documentation`)
- [ ] 5.2 Update `docs/formats.md` stored-digests table (zlib `adler32`; lzip
      multi-member combined `crc32`; note derivation)
- [ ] 5.3 Extend corpus / focused tests for zlib + multi-lzip parity rows

## 6. Verify

- [ ] 6.1 Targeted tests for combine helpers, zlib Adler peek, multi-lzip CRC
- [ ] 6.2 `uv run --no-sync pytest` for affected tests; `ruff format` / check on
      touched paths
- [ ] 6.3 `openspec validate --strict surface-stored-stream-digests`
