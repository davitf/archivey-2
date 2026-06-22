# Archive Data Model — delta (Phase 3)

Phase 3 reads standalone `lzip` / `zlib` / `brotli` / `unix-compress` streams, but the
`StreamFormat` enum only defines `gz`/`bz2`/`xz`/`zst`/`lz4`, so those formats cannot be
named. The enum is already documented as extensible ("new outer codecs are added here
(lzip, brotli, …)"); this change makes that concrete.

## MODIFIED Requirements

### Requirement: Archive format identity (ArchiveFormat)

The system SHALL model a format as the combination of a **container** and a **stream**
codec, rather than a single flat enum. `ContainerFormat` names the member layout (zip,
tar, 7z, …) and `StreamFormat` names the outer single-stream codec the container is
wrapped in (gzip, xz, … or `UNCOMPRESSED`). `ArchiveFormat` is a frozen
`(container, stream)` dataclass; the familiar named formats (`ZIP`, `TAR_GZ`,
`SEVEN_Z`, …) are predefined class-var instances, so callers keep writing
`ArchiveFormat.TAR_GZ` while the model underneath is compositional.

`StreamFormat` SHALL cover every outer single-stream codec the library can read as a
standalone stream, so that single-file and `tar.<codec>` formats are all expressible:

```python
class StreamFormat(StrEnum):
    UNCOMPRESSED  = "uncompressed"
    GZIP          = "gz"
    BZIP2         = "bz2"
    XZ            = "xz"
    ZSTD          = "zst"     # requires [zstd] extra
    LZ4           = "lz4"     # requires [lz4] extra
    LZIP          = "lz"
    ZLIB          = "zz"
    BROTLI        = "br"
    UNIX_COMPRESS = "Z"       # requires [unix-compress] extra
    # extensible: further outer codecs are added here
```

Named standalone `ArchiveFormat` constants SHALL exist for the bare single-stream
formats — `GZ`, `BZ2`, `XZ`, `ZST`, `LZIP`, `ZLIB`, `BROTLI`, `Z` (each
`RAW_STREAM × <codec>`) — alongside the container constants (`ZIP`, `TAR`, `TAR_GZ`, …).
Container × codec combinations that are **not in common practice** (e.g. `tar.lz`,
`tar.br`) SHALL NOT get a predefined constant; they are constructed on demand as
`ArchiveFormat(container, stream)` and compare equal to any other instance with the same
pair. `file_extension()` derives from the codec for a `RAW_STREAM` (e.g. `Z` → `"Z"`,
`LZIP` → `"lz"`) and from `container.codec` for a container (e.g. `TAR × LZIP` → `"tar.lz"`).

#### Scenario: format identity round-trips through the (container, stream) pair

- **WHEN** a caller compares `ar.format` against `ArchiveFormat(ContainerFormat.TAR, StreamFormat.GZIP)`
- **THEN** it is equal to `ArchiveFormat.TAR_GZ`

#### Scenario: a standalone lzip stream has a named format

- **WHEN** a `.lz` (lzip) stream is opened
- **THEN** `ar.format == ArchiveFormat.LZIP`, whose `container == ContainerFormat.RAW_STREAM` and `stream == StreamFormat.LZIP`

#### Scenario: an uncommon container×codec combination is built on demand

- **WHEN** a `tar.lz` (tar wrapped in lzip) source is opened
- **THEN** `ar.format == ArchiveFormat(ContainerFormat.TAR, StreamFormat.LZIP)` even though no `TAR_LZIP` class constant is predefined
- **AND** `ar.format.file_extension() == "tar.lz"`
