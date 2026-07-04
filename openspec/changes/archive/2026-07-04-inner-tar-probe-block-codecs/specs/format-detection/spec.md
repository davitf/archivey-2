# Format Detection — delta (inner-tar-probe-block-codecs)

## MODIFIED Requirements

### Requirement: Compressed streams are probed for an inner TAR

Detection SHALL probe a single-file compressor (gzip, bzip2, xz, zstd, lz4, lzip, zlib,
brotli, unix-compress) for an inner TAR by decompressing a bounded amount of its *content*
and testing for the TAR `ustar` signature at offset 257, so a tarball is reported as the
combined format (`TAR_GZ` / `TAR_BZ2` / `TAR_XZ` / `TAR_ZST` / `TAR_LZ4`, and likewise
the TAR + lzip/zlib/brotli combination) rather than a bare single-file compressor. The
probe SHALL decompress only enough to reach the TAR header region (≥ 512 decompressed
bytes).

Reaching the header region requires different amounts of *compressed* input by codec:

- A **stream-oriented** codec (gzip, xz, zstd, lz4, lzip, zlib, brotli, unix-compress) emits
  decompressed output incrementally, so the ordinary peeked detection prefix
  (`DETECTION_LIMIT` bytes) already reaches the header region.
- A **block-transform** codec (bzip2) emits nothing until an entire block has been read (a
  bzip2 block holds up to 900 KB uncompressed). When the first block's compressed size
  exceeds the peeked prefix, the prefix decodes to no usable output. In that case the probe
  SHALL read further from the **source** — up to one maximum block
  (`_INNER_TAR_MAX_PROBE_BYTES`, ≥ the largest bzip2 block's compressed size, so a full first
  block is always available) — and retry the decode. This read is bounded, and like the
  prefix peek it MUST NOT consume the source: a seekable source is read and restored to its
  starting position, a path is opened and closed, and a non-seekable source is buffered in
  its `PeekableStream` so the bytes replay to the backend.

The probe SHALL use the sequential decompression backend, so a random-access accelerator
(e.g. rapidgzip) that rejects a bounded/truncated prefix is not engaged.

If the compressor's decompression backend is unavailable, detection reports the bare
compressor format and defers the inner-TAR determination to open time. If, after reading up
to the maximum block, the decoded output still carries no TAR header (or the stream is
genuinely truncated), detection reports the bare compressor format.

#### Scenario: gzip wrapping a tar

- **WHEN** a `.gz` stream decompresses to bytes carrying `ustar` at offset 257
- **THEN** `detect_format()` returns `ArchiveFormat.TAR_GZ` (not bare `GZIP`)

#### Scenario: gzip wrapping a single file

- **WHEN** a `.gz` stream decompresses to content with no TAR signature
- **THEN** `detect_format()` returns `ArchiveFormat.GZIP` (a one-member single-file compressor)

#### Scenario: bzip2 wrapping a tar whose first block exceeds the detection prefix

- **WHEN** a `.tar.bz2` stream's first bzip2 block compresses to more than the peeked
  detection prefix (e.g. a leading member of incompressible data), so the prefix alone yields
  no decompressed output
- **THEN** the probe reads up to one maximum block from the source, decodes the TAR header
  region, and `detect_format()` returns `ArchiveFormat.TAR_BZ2` — not bare `BZ2`

#### Scenario: large-block bzip2 that is not a tar stays bare

- **WHEN** a bare `.bz2` stream with a large first block decompresses to content with no TAR
  signature
- **THEN** the probe reads up to one maximum block, finds no `ustar`, and `detect_format()`
  returns `ArchiveFormat.BZ2` (no false promotion; the read stays bounded)

#### Scenario: inner-tar probe over a non-seekable source is not consumed

- **WHEN** a `.tar.bz2` is detected from a non-seekable source wrapped in a `PeekableStream`
  and the probe must read a full block to reach the header region
- **THEN** the block is buffered in the `PeekableStream`, `detect_format()` returns
  `ArchiveFormat.TAR_BZ2`, and the backend can still read the whole archive afterwards
