## 1. Single-file gzip/lzip trailer CRC

- [x] 1.1 gzip: when single-member + seekable/path source, peek the 8-byte trailer and set `member.hashes["crc32"]`; reuse the truncation backstop's member-count detection to omit on multi-member
- [x] 1.2 lzip: surface the per-member trailer CRC-32 via the seekable lzip backend hook that already yields size
- [x] 1.3 Omit `crc32` on non-seekable sources and for bz2/xz/zlib/br/`.Z`; confirm no decompression pass is triggered

## 2. Parity sweep + docs

- [x] 2.1 Extend `tests/test_corpus_sweep.py` with the stored-digest parity matrix (present where documented, absent where not)
- [x] 2.2 Add the stored-digest matrix + cheap-dedupe recipe to the end-user guide (`docs/`)

## 3. Tests

- [x] 3.1 `test_single_member_gzip_exposes_stored_crc32` (present) and `test_multi_member_gzip_omits_crc32` (absent)
- [x] 3.2 `test_lzip_exposes_stored_crc32` via seekable backend; skip when backend/source unavailable
- [x] 3.3 Assert read behavior + verification unchanged when the stored CRC is surfaced

## 4. Verify

- [ ] 4.1 Run the parity sweep across `[all]`, `[all-lowest]`, `core-only`
- [ ] 4.2 `openspec validate --strict stored-digest-dedupe-parity`
