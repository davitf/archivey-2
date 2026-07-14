## 1. Raw member-data path

- [ ] 1.1 Bounded local-file-header parse: from `ZipInfo.header_offset`, read the 30-byte header + local name/extra lengths to compute the data start (reject absurd lengths)
- [ ] 1.2 `SlicingStream(source, data_start, compress_size)` yields the raw compressed member stream, under the existing handle-lock / SharedSource discipline
- [ ] 1.3 Dispatch by method id via the existing `StreamCodec` map; STORED = passthrough slice

## 2. Codec + verification wiring

- [ ] 2.1 Route unencrypted members through the codec-layer stream; wrap in `VerifyingStream` with `member.hashes["crc32"]`
- [ ] 2.2 Missing optional backend → `PackageNotInstalledError`; corrupt body → `CorruptionError` (drop the codec-path `NotImplementedError`/bare-`EOFError` mapping for these)
- [ ] 2.3 Keep encrypted (ZipCrypto / AE) members on the current `zipfile` decryption path

## 3. Extended codecs + open questions

- [ ] 3.1 Confirm ZIP method-98 PPMd parameter-header framing and method-93 zstd framing against a real producer/oracle before enabling
- [ ] 3.2 Decide `inflate64` packaging for ZIP Deflate64 (reuse `[7z]` vs shared/`[recommended]`); document it
- [ ] 3.3 Deflate64/Zstd/PPMd ZIP fixtures generated on demand (7-Zip/WinZip); skip when producer absent

## 4. Tests + verify

- [ ] 4.1 Round-trip STORED/DEFLATE/BZIP2/LZMA unchanged; Deflate64/Zstd/PPMd now decode
- [ ] 4.2 Missing-backend → `PackageNotInstalledError`; corrupt → `CorruptionError`; encrypted behavior unchanged
- [ ] 4.3 Free-threaded parallel member reads still correct (CONCURRENT + `3.13t`)
- [ ] 4.4 Run across `[all]`, `[all-lowest]`, `core-only`; `openspec validate --strict zip-native-codec-streams`
