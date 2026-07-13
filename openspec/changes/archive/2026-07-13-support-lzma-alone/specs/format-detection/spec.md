## MODIFIED Requirements

### Requirement: Magic-less formats are detected by a content probe

When the magic-byte table yields no match, the system SHALL run each registered
content probe on the peeked prefix (consumes nothing). This covers Brotli (no
signature), zlib (too-unspecific CMF/FLG), and LZMA Alone (13-byte header whose
properties byte is too weak for exact magic). Match → `PROBABLE` /
`detected_by="content_probe"`. Probes typically decode a bounded prefix; MAY gate
on cheap structural bytes first. Skip when the decompressor backend is missing
(fall through to extension). Extension MAY override a disagreeing probe
(false-positive risk on short/adversarial input).

The LZMA Alone probe SHALL attempt a bounded `FORMAT_ALONE` decode and MUST NOT
claim streams that already matched exact magic (notably lzip `LZIP` and xz
`FD 37 7A…`).

#### Scenario: content-probe matrix

| Case | Expected |
| --- | --- |
| No magic; bounded prefix decompresses as Brotli | `BROTLI`, `PROBABLE`, `content_probe` |
| zlib CMF/FLG + clean zlib decode | `ZLIB`, `PROBABLE`, `content_probe` |
| zlib-looking header, decode fails | No zlib claim; fall through to extension / fail |
| `.br`, Brotli extra missing | Probe skipped; extension guess `BROTLI`/`GUESS` |
| No magic; bounded prefix decompresses as LZMA Alone | `LZMA_ALONE`, `PROBABLE`, `content_probe` |
| Stream starts with `LZIP` | lzip magic wins; Alone probe not claimed |
| Alone-looking bytes that fail `FORMAT_ALONE` decode | No Alone claim; fall through |

### Requirement: Compressed streams are probed for an inner TAR

For single-file compressors (gzip, bzip2, xz, zstd, lz4, lzip, LZMA Alone, zlib,
brotli, unix-compress), detection SHALL decompress a bounded amount of *content*
and look for TAR `ustar` at offset 257, reporting combined formats (`TAR_GZ`, …)
when present. Need ≥512 decompressed bytes.

Compressed input is supplied via a **bounded, non-consuming view** (up to
`_INNER_TAR_MAX_PROBE_BYTES`, ≥ largest bzip2 first-block compressed size):

- Stream codecs pull incrementally (first few KiB usually enough).
- Block-transform (bzip2) may pull a full first block before any output.

Seekable: read + restore position. Path: open/close. Non-seekable: buffer in
`PeekableStream` for replay. Use sequential decompression (not random-access
accelerators that reject bounded non-seekable views). Missing decompressor → bare
compressor format; open may refine. No TAR header within the bound → bare
compressor.

#### Scenario: inner-TAR matrix

| Case | Expected |
| --- | --- |
| `.gz` → content with `ustar`@257 | `TAR_GZ` (not bare `GZIP`) |
| `.gz` → non-TAR content | `GZIP` |
| `.tar.bz2` with large first block (> peek prefix) | Read up to max block; `TAR_BZ2` |
| Large-block bare `.bz2`, no `ustar` | Bounded read; `BZ2` (no false promotion) |
| Non-seekable `.tar.bz2` needing full block | Buffered in `PeekableStream`; `TAR_BZ2`; backend can still read all |
| Alone `.tar.lzma` / Alone `.tlz` with `ustar`@257 | `ArchiveFormat(TAR, LZMA_ALONE)` |
| Bare Alone `.lzma`, no `ustar` | `ArchiveFormat.LZMA_ALONE` |

## ADDED Requirements

### Requirement: Keep `.tlz` as TAR × LZIP; Alone content still wins

The system SHALL keep the TAR short alias `.tlz` mapped to
`ArchiveFormat(ContainerFormat.TAR, StreamFormat.LZIP)` (same family as `.lz` /
`.tar.lz`). Canonical Alone paths remain `.lzma` / `.tar.lzma`. Content detection
SHALL still win over the extension alias:

| Leading bytes | Detected format |
| --- | --- |
| Exact `LZIP` magic | LZIP (then inner-TAR probe may yield TAR × LZIP) |
| Alone content probe match | LZMA Alone (then inner-TAR probe may yield TAR × LZMA_ALONE) |

A `.tlz` whose content is LZMA Alone SHALL detect as TAR × LZMA_ALONE and emit
`FORMAT_EXTENSION_CONFLICT` against the lzip alias. A `.tlz` whose content is
lzip SHALL detect as TAR × LZIP with no Alone claim.

#### Scenario: `.tlz` / Alone extension matrix

| Case | Expected |
| --- | --- |
| `test_compat_lzip_1.tlz` (`LZIP` magic + TAR) | TAR × LZIP; no Alone claim |
| `test_compat_lzma_*.tlz` (Alone payload + TAR) | `ArchiveFormat(TAR, LZMA_ALONE)`; members readable; `FORMAT_EXTENSION_CONFLICT` retained under default budget |
| Extension-only `.tlz` with unreadable/empty content | GUESS `ArchiveFormat(TAR, LZIP)` |
| Bare `.lzma` Alone, no TAR | `ArchiveFormat.LZMA_ALONE` |
| `.tar.lzma` Alone + TAR | `ArchiveFormat(TAR, LZMA_ALONE)` |
