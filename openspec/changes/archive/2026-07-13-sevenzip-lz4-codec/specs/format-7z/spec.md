## MODIFIED Requirements

### Requirement: Decode folder coder chains through compressed-streams

The system SHALL decode each folder by composing shared `compressed-streams`
backends in decoding order. A coder list such as `AES -> LZMA2` decrypts, then
decompresses. Files in a folder are yielded by reading exactly `member.size`
bytes in archive order from the decompressed folder stream. Per-member CRC32
values SHALL appear in `hashes["crc32"]` and SHALL be verified by the shared
verification stage as data is read.

| 7z codec | Method ID | Backend | Availability |
| --- | --- | --- | --- |
| STORED | `0x00` | pass-through | core |
| LZMA1 / LZMA2 | `0x030101` / `0x21` | `lzma` `FORMAT_RAW` | core |
| Delta | `0x03` | `lzma.FILTER_DELTA` | core |
| BCJ x86/ARM/ARMT/PPC/SPARC/IA64 | `0x04`-`0x09`, `0x03030103`... | `lzma` BCJ filters (LZMA2+BCJ); `pybcj` for LZMA1+BCJ | core for LZMA2+BCJ; `[7z]` for LZMA1+BCJ |
| Deflate | `0x040108` | raw `zlib` | core |
| BZip2 | `0x040202` | `bz2` | core |
| Zstd | `0x04f71101` | stdlib `compression.zstd` / `backports.zstd` | core on 3.14+; otherwise `[7z]` |
| Brotli | `0x04f71102` | `brotli` | `[7z]` |
| LZ4 | `0x04f71104` | `lz4` frame decoder (same backend as standalone / `.tar.lz4`) | `[lz4]` (also pulled by `[7z]`) |
| PPMd (var.H) | `0x030401` | `pyppmd` | `[7z]` |
| Deflate64 | `0x040109` | `inflate64` | `[7z]` |
| AES-256 / SHA-256 | `0x06f10701` | crypto backend | `[crypto]` / `[7z]` |
| BCJ2 | `0x0303011B` | none | unsupported |

The `[7z]` extra SHALL provide PPMd, Deflate64, Zstd on Python versions without
stdlib zstd, Brotli, LZ4, AES, and LZMA1+BCJ (`pybcj`) support in one install.

LZMA1+BCJ folders SHALL NOT be decoded via a single combined `lzma` `FORMAT_RAW`
filter chain: liblzma can silently truncate the final BCJ look-ahead bytes when
LZMA1 lacks an end-of-stream marker. The reader MUST stage LZMA1 (and any non-BCJ
`lzma` filters such as Delta) through stdlib `lzma`, then apply each BCJ stage
through `pybcj`. LZMA2+BCJ remains a single stdlib filter chain in core.

#### Scenario: coder-chain matrix

| Case | Expected |
| --- | --- |
| BCJ + LZMA2 folder | Shared `lzma` raw filter chain returns original bytes |
| BCJ + LZMA1 folder with `[7z]` / `pybcj` | Staged LZMA1 then `pybcj` returns original bytes |
| BCJ + LZMA1 folder without `pybcj` | `PackageNotInstalledError` names `pybcj` and the `[7z]` extra |
| Member with stored CRC32 | Terminal verification raises `CorruptionError` on mismatch |
| PPMd without `[7z]` | `PackageNotInstalledError` names `pyppmd` and the `[7z]` extra |
| AES + LZMA2 folder | Crypto stage decrypts before LZMA2 decompression |
| LZ4 folder (`0x04f71104`) with `lz4` installed | Shared `Codec.LZ4` returns original bytes |
| LZ4 folder without `lz4` | `PackageNotInstalledError` names `lz4` and the `[lz4]` / `[7z]` extra |
