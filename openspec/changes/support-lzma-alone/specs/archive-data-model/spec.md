## MODIFIED Requirements

### Requirement: Archive format identity is compositional

The system SHALL model archive format as a frozen `(container, stream)` pair:
`ContainerFormat` names the member layout (`zip`, `tar`, `7z`, raw stream, ...),
and `StreamFormat` names the outer single-stream codec (`gz`, `xz`, ... or
`UNCOMPRESSED`). Predefined constants such as `ArchiveFormat.ZIP`,
`ArchiveFormat.TAR_GZ`, `ArchiveFormat.SEVEN_Z`, and standalone
`ArchiveFormat.LZIP` SHALL be class-var instances of that pair.

`StreamFormat` SHALL cover every standalone outer codec Archivey can read:
`UNCOMPRESSED`, `GZIP`, `BZIP2`, `XZ`, `ZSTD`, `LZ4`, `LZIP`, `LZMA_ALONE`,
`ZLIB`, `BROTLI`, and `UNIX_COMPRESS`. Standalone raw-stream constants SHALL
exist for `GZ`, `BZ2`, `XZ`, `ZST`, `LZ4`, `LZIP`, `LZMA_ALONE`, `ZLIB`,
`BROTLI`, and `Z`. Uncommon container-codec pairs such as `tar.lz` and
`tar.lzma` SHALL be constructed on demand as `ArchiveFormat(container, stream)`
rather than receiving named constants. `file_extension()` SHALL derive from the
stream for raw streams and from `container.codec` for containers.

`StreamFormat.LZMA_ALONE` SHALL name the legacy LZMA Alone file format
(`lzma.FORMAT_ALONE`: 13-byte header with properties, dictionary size, and
uncompressed size — not raw LZMA). Its enum value SHALL be `"lzma"` so
`file_extension()` yields `lzma` / `tar.lzma`. It MUST NOT be confused with raw
7z/ZIP `FORMAT_RAW` LZMA1/LZMA2 (`Codec.LZMA` / `Codec.LZMA2`).

#### Scenario: format identity matrix

| Case | Expected |
| --- | --- |
| Compare `ar.format` to `ArchiveFormat(ContainerFormat.TAR, StreamFormat.GZIP)` | Equal to `ArchiveFormat.TAR_GZ` |
| Open a `.lz` standalone lzip stream | `ArchiveFormat.LZIP`; container `RAW_STREAM`; stream `LZIP` |
| Open `tar.lz` | Equal to `ArchiveFormat(ContainerFormat.TAR, StreamFormat.LZIP)`; `file_extension() == "tar.lz"` |
| Open a `.lzma` Alone stream | `ArchiveFormat.LZMA_ALONE`; container `RAW_STREAM`; stream `LZMA_ALONE` |
| Open `tar.lzma` | Equal to `ArchiveFormat(ContainerFormat.TAR, StreamFormat.LZMA_ALONE)`; `file_extension() == "tar.lzma"` |
