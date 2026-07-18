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
| `.Z` / `.tar.Z` | yes | — | — | CLEAR seek points when seekable | Best-effort truncation (nonzero leftover bits) |

Recommended installs: `archivey[recommended]` or `archivey[recommended-lite]` (no
`rapidgzip`). Full codec rationale: [library analysis](internal/library-analysis.md).
Third-party credits (deps, oracles, design refs): [Acknowledgements](acknowledgements.md).

## ZIP

- Stdlib ``zipfile`` for **central-directory parsing / listing**; member **data** decodes
  through archivey's shared codec layer (seekable source only, even with
  ``streaming=True``).
- Extended ZIP codecs when their extras are installed: Deflate64 and PPMd via ``[7z]``
  (``inflate64`` / ``pyppmd`` — same packages as the 7z optional codecs), Zstd via
  ``[zstd]`` (or stdlib on 3.14+). A missing backend raises ``PackageNotInstalledError``.
- Multi-volume / split ZIP (``.z01``…``.zip``) is detected and rejected with
  ``UnsupportedFeatureError`` — rejoin first.
- Unsupported compression methods: listing succeeds; reading raises
  ``UnsupportedFeatureError``.
- Timestamps: DOS base; NTFS / Extended Timestamp extras override when present.
- **Member-name encoding.** Names flagged UTF-8 decode as UTF-8. For an unflagged name
  (APPNOTE says cp437), many tools nonetheless write UTF-8 without setting the flag, so
  Archivey prefers UTF-8 when the stored bytes are valid UTF-8, and otherwise falls back
  to a configurable legacy encoding (`ArchiveyConfig.zip_unflagged_fallback_encoding`,
  default `cp437`). When UTF-8 is inferred for an unflagged name, a
  `member_name_encoding_inferred` diagnostic records it. Passing `encoding=` to
  `open_archive` is authoritative — it is used verbatim and disables the sniff.
- ZipCrypto multi-password confirmation can be expensive on **STORED** members — see
  [costs](costs.md). **WinZip AES** (method 99 / AE-1 and AE-2) decrypts via the
  `[crypto]` extra (PBKDF2 + AES-CTR + HMAC-SHA1); AE-2 members expose no `crc32`
  (integrity is the HMAC). Absent `[crypto]`, an AES member raises
  `PackageNotInstalledError` but is still listed as encrypted.

## TAR (and compressed TAR)

- Uncompressed seekable TAR: random access via `tarfile`.
- Compressed variants (`.tar.gz` etc.) behave as **solid** for random member opens —
  prefer a single forward pass.
- Hardlinks are first-class at extraction; unfiltered `extract_all` resolves them in one
  pass.
- `MemberStreams.CONCURRENT` uses a per-reader shared-handle lock (same shape as ISO).
- **Mid-archive corruption can silently shorten the listing.** Stdlib `tarfile` treats a
  corrupt member header *after the first* as a clean end of archive — no exception is
  raised; iteration just stops early. Archivey backstops this with its end-of-archive
  marker check: a listing cut short by corruption almost never lands on a valid
  two-block null trailer, so it surfaces as the `ARCHIVE_EOF_MARKER_MISSING` diagnostic
  (a warning by default). When a provably complete listing matters (inventory/dedupe
  sweeps), set `ArchiveyConfig(strict_archive_eof=True)` to escalate that diagnostic to
  `TruncatedError`.

## 7z

- **Native** header parse + stdlib codecs for the common set (LZMA/LZMA2/BCJ/Delta/
  Deflate/BZip2/stored). No `py7zr` on the read path.
- `[7z]` adds PPMd, Deflate64, Zstd, Brotli, and AES (via crypto).
- **BCJ2** is detected and rejected (`UnsupportedFeatureError`) — never garbage output.
- Solid folders: `stream_members()` decodes each folder once; random `open()` of a mid-
  folder member may re-decode from the folder start.
- **AES + store/copy with no folder digest and no member CRC:** 7z has no password check
  value; a wrong password can yield garbage (matches 7-Zip). Archivey emits
  `DIGEST_UNVERIFIABLE` (`reason="no_integrity_anchor"`). See [Gotchas](gotchas.md#passwords-that-look-accepted).
- `NumCyclesPower` is capped at ≤24 or the `0x3F` no-hash sentinel (7-Zip’s own clamp);
  values 25–62 raise `UnsupportedFeatureError`.
- Writing needs `[7z-write]` (`py7zr`).

## RAR

- Metadata / listing: native RAR 1.5–RAR5 parser (works without `unrar`).
- Member **data**: RARLAB `unrar` on `PATH` (not `unrar-free` / `unar`). Passwords are
  passed as bare `-p` with the secret on stdin (not in argv).
- `[rar]` / `[crypto]`: header-encrypted RAR5 and Blake2sp verification. RAR5 members
  with the HASHMAC flag verify tweaked digests via UnRAR’s `ConvertHashToMAC` when a
  password is available; tweaked values are not exposed as plain `member.hashes`.
- **File-version history (`-ver`):** revision rows appear in `members()` as names like
  `path;1` with `extra["rar.file_version"]` and `is_current=False`; the live path stays
  `is_current=True`. Default extract **skips** non-current rows.
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
- `.gz` surfaces the trailer CRC-32 as `member.hashes["crc32"]` for a **single-member**
  file on a seekable/path source (omit for multi-member gzip — the trailer covers only
  the last member — and for non-seekable sources).
- `.lz` surfaces the trailer CRC-32 the same way **size** is exposed: only when
  `MemberStreams.SEEKABLE` is declared on a path source (seekable lzip backend).
- `.bz2` / `.xz` / zlib / brotli / `.Z` have no cheap whole-member stored CRC.
- `.Z` (unix-compress) is core (native LZW). Truncation is best-effort: nonzero leftover
  bits after the last complete code raise `TruncatedError` on the next `read()` after
  delivering available bytes; zero-leftover cuts remain silent. Forward decode works on
  non-seekable sources; CLEAR boundaries provide seek points when seekability is declared.
- `archivey.open_stream(...)` matches the archive rule: non-seekable unless
  `seekable=True`.

## Stored digests (cheap dedupe)

`member.hashes` holds digests the archive **already stores**, keyed by algorithm
(`"crc32"` as `int`, `"blake2sp"` as `bytes`). They are readable without decompressing
when the backend documents them. They are **not** computed digests — a full `read()` still
verifies through the normal path.

| Format | When present | Keys |
| --- | --- | --- |
| ZIP | FILE / SYMLINK (central directory) | `crc32` |
| 7z | FILE | `crc32` |
| RAR5 | FILE with CRC32 and/or Blake2sp | `crc32` and/or `blake2sp` |
| single-file `.gz` | single member, seekable/path | `crc32` |
| single-file `.lz` | seekable path + `MemberStreams.SEEKABLE` | `crc32` |
| `.bz2` / `.xz` / zlib / brotli / `.Z`, TAR, directory | — | none |

See [usage](usage.md#cheap-dedupe-with-stored-hashes) for the cheap→computed fallback recipe.

## Detection

- Magic bytes first, then extension; wrong extensions are expected.
- Self-extracting (SFX) stubs are detected when the archive payload sits behind an
  executable header.
- Confidence and evidence are part of `detect_format` / `FormatInfo` — see
  `format-detection` spec.
