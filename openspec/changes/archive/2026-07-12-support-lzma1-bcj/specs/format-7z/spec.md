## MODIFIED Requirements

### Requirement: Decode linear 7z coder chains through shared stream backends

The system SHALL decode each folder's coder list as a linear pipeline of shared stream
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
| BCJ x86/ARM/ARMT/PPC/SPARC/IA64 | `0x04`-`0x09`, `0x03030103`... | `lzma` BCJ filters (with LZMA2); `pybcj` when chained with LZMA1 | core for LZMA2+BCJ; `[7z]` for LZMA1+BCJ |
| Deflate | `0x040108` | raw `zlib` | core |
| BZip2 | `0x040202` | `bz2` | core |
| Zstd | `0x04f71101` | stdlib `compression.zstd` / `backports.zstd` | core on 3.14+; otherwise `[7z]` |
| Brotli | `0x04f71102` | `brotli` | `[7z]` |
| PPMd (var.H) | `0x030401` | `pyppmd` | `[7z]` |
| Deflate64 | `0x040109` | `inflate64` | `[7z]` |
| AES-256 / SHA-256 | `0x06f10701` | crypto backend | `[crypto]` / `[7z]` |
| BCJ2 | `0x0303011B` | none | unsupported |

The `[7z]` extra SHALL provide PPMd, Deflate64, Zstd on Python versions without
stdlib zstd, Brotli, AES, and LZMA1+BCJ (`pybcj`) support in one install.

LZMA1+BCJ folders SHALL NOT be decoded via a single combined `lzma` `FORMAT_RAW`
filter chain: liblzma can silently truncate the final BCJ look-ahead bytes when
LZMA1 lacks an end-of-stream marker (common from the 7-Zip CLI). The reader MUST
stage LZMA1 (and any non-BCJ `lzma` filters such as Delta) through stdlib `lzma`,
then apply each BCJ stage through `pybcj` (`import bcj`). LZMA2+BCJ remains a
single stdlib filter chain in core.

#### Scenario: coder-chain matrix

| Case | Expected |
| --- | --- |
| BCJ + LZMA2 folder | Shared `lzma` raw filter chain returns original bytes |
| BCJ + LZMA1 folder with `[7z]` / `pybcj` | Staged LZMA1 then `pybcj` returns original bytes (including 7-Zip CLI fixtures that truncate under combined liblzma) |
| BCJ + LZMA1 folder without `pybcj` | `PackageNotInstalledError` names `pybcj` and the `[7z]` extra |
| Member with stored CRC32 | Terminal verification raises `CorruptionError` on mismatch |
| PPMd without `[7z]` | `PackageNotInstalledError` names `pyppmd` and the `[7z]` extra |
| AES + LZMA2 folder | Crypto stage decrypts before LZMA2 decompression |

### Requirement: Reject unsupported codecs without fallback

The system SHALL raise `UnsupportedFeatureError` naming the codec or method ID
when a folder uses a coder with no available backend. This includes BCJ2, newer
branch filters absent from installed liblzma, and unrecognized method IDs. The
reader MUST NOT return garbage and MUST NOT fall back to `py7zr` or another
third-party reader. PPMd, Deflate64, and LZMA1+BCJ are optional-supported via
`[7z]`, and multi-volume 7z is supported by volume joining.

#### Scenario: unsupported-codec matrix

| Case | Expected |
| --- | --- |
| Folder uses BCJ2 | `UnsupportedFeatureError` names BCJ2; no output bytes |
| Folder uses unknown method ID | `UnsupportedFeatureError` names the method ID |
| Folder uses PPMd with `[7z]` installed | Member is decoded, not rejected |
| Folder uses LZMA1+BCJ with `[7z]` installed | Member is decoded via staged `pybcj`, not rejected |

## ADDED Requirements

### Requirement: Stage LZMA1+BCJ through pybcj under `[7z]`

The system SHALL decode linear folders whose coder chain includes both LZMA1 and
at least one BCJ branch filter (x86/ARM/ARMT/PPC/SPARC/IA64) by composing
stdlib LZMA1 decompression with `pybcj` BCJ filters. The reader MUST NOT feed
LZMA1 and BCJ into one `lzma.LZMADecompressor` `FORMAT_RAW` filter list. When
`pybcj` is absent, opening such a member SHALL raise `PackageNotInstalledError`
naming `pybcj` and `pip install archivey[7z]`. BCJ2 remains unsupported.

#### Scenario: LZMA1+BCJ matrix

| Case | Expected |
| --- | --- |
| 7-Zip CLI `-m0=BCJ -m1=LZMA` fixture + `pybcj` | Round-trip bytes match; no silent 4-byte truncation |
| py7zr `FILTER_X86`+`FILTER_LZMA` fixture + `pybcj` | Round-trip bytes match |
| Same fixtures without `pybcj` | `PackageNotInstalledError` for `pybcj` / `[7z]` |
| LZMA2+BCJ without `pybcj` | Still works in core via stdlib filters |
