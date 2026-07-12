# Formats and extras

What each format can do, what optional packages or tools it needs, and the quirks that
most often surprise callers. Authoritative detail lives in `openspec/specs/format-*`.

## Quick matrix

| Format | Core? | Extra / tool | Listing | Random member access | Notes |
| --- | --- | --- | --- | --- | --- |
| ZIP | yes | — | indexed (central directory) | direct | Seekable source required |
| TAR | yes | — | scan headers | direct on uncompressed seekable TAR | Compressed TAR is solid for random opens |
| `.tar.gz` / `.bz2` / `.xz` | yes | — | needs decompression | solid | Prefer `stream_members()` |
| Directory | yes | — | indexed | direct | Same stream-capability defaults as archives |
| Single-file gz/bz2/xz | yes | — | one member | seek with `SEEKABLE` | See single-file section |
| 7z | yes (common codecs) | `[7z]` for PPMd/Deflate64/zstd/brotli/AES | indexed | solid folders | Native reader; BCJ2 unsupported |
| RAR | yes (metadata) | `unrar` for data; `[rar]` for header crypto / Blake2sp | native metadata | solid when solid | No write |
| ISO | no | `[iso]` (`pycdlib`) | indexed | direct | Seekable source required |
| `.zst` / `.tar.zst` | 3.14+ core; else `[zstd]` | `[zstd]` → `backports.zstd` | — | rewind seek unless indexed later | |
| `.lz4` / `.tar.lz4` | no | `[lz4]` | — | rewind seek | |
| `.Z` / `.tar.Z` | no | `[unix-compress]` | — | special | Truncation undetectable |

Recommended installs: `archivey[recommended]` or `archivey[recommended-lite]` (no
`rapidgzip`). Full codec rationale: [library analysis](internal/library-analysis.md).

## ZIP

- Stdlib `zipfile` backend; seekable source only (even with `streaming=True`).
- Multi-volume / split ZIP (`.z01`…`.zip`) is detected and rejected with
  `UnsupportedFeatureError` — rejoin first.
- Unsupported compression methods: listing succeeds; reading raises
  `UnsupportedFeatureError`.
- Timestamps: DOS base; NTFS / Extended Timestamp extras override when present.
- ZipCrypto multi-password confirmation can be expensive on **STORED** members — see
  [costs](costs.md).

## TAR (and compressed TAR)

- Uncompressed seekable TAR: random access via `tarfile`.
- Compressed variants (`.tar.gz` etc.) behave as **solid** for random member opens —
  prefer a single forward pass.
- Hardlinks are first-class at extraction; unfiltered `extract_all` resolves them in one
  pass.
- `MemberStreams.CONCURRENT` uses a per-reader shared-handle lock (same shape as ISO).

## 7z

- **Native** header parse + stdlib codecs for the common set (LZMA/LZMA2/BCJ/Delta/
  Deflate/BZip2/stored). No `py7zr` on the read path.
- `[7z]` adds PPMd, Deflate64, Zstd, Brotli, and AES (via crypto).
- **BCJ2** is detected and rejected (`UnsupportedFeatureError`) — never garbage output.
- Solid folders: `stream_members()` decodes each folder once; random `open()` of a mid-
  folder member may re-decode from the folder start.
- Writing needs `[7z-write]` (`py7zr`).

## RAR

- Metadata / listing: native RAR 1.5–RAR5 parser (works without `unrar`).
- Member **data**: RARLAB `unrar` on `PATH` (not `unrar-free` / `unar`).
- `[rar]` / `[crypto]`: header-encrypted RAR5 and Blake2sp verification.
- Solid archives: one `unrar p` pipe for `stream_members()`; random solid opens may use
  explicit temp materialization.
- Read-only — no RAR writer.

## ISO 9660

- Needs `[iso]` (`pycdlib`) and a seekable source.
- Namespace auto-selected: Rock Ridge → Joliet → plain ISO 9660; reported in
  `ArchiveInfo.extra["iso.namespace"]`.
- Raw `.bin` Mode 1 sector images may be stripped to 2048-byte payloads; unsupported
  layouts raise rather than mis-read.

## Directory

- A filesystem tree as a pseudo-archive (uniform API for tests and dir↔archive flows).
- Same default stream contract as archives: forward-only, one live stream, until you
  declare `SEEKABLE` / `CONCURRENT`.

## Single-file compressors

- One synthetic member (name from the source path, or `data` for anonymous streams).
- `.gz` may expose `extra["gzip.original_filename"]` when the header carries `FNAME`.
- `.Z` (unix-compress) needs `[unix-compress]`; truncation cannot be detected (format
  carries no length/checksum).
- `archivey.open_stream(...)` matches the archive rule: non-seekable unless
  `seekable=True`.

## Detection

- Magic bytes first, then extension; wrong extensions are expected.
- Self-extracting (SFX) stubs are detected when the archive payload sits behind an
  executable header.
- Confidence and evidence are part of `detect_format` / `FormatInfo` — see
  `format-detection` spec.
