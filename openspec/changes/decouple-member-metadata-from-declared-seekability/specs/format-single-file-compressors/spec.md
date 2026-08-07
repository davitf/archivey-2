# format-single-file-compressors — metadata/seekability decoupling delta

## MODIFIED Requirements

### Requirement: Report member size with codec caveats

The system SHALL populate `member.size` according to codec metadata and
format-specific reliability limits:

| Codec | `member.size` behavior |
| --- | --- |
| GZ | Always `None`; stored ISIZE is modulo 2^32 and may be wrong |
| BZ2, ZLIB, BR, Z | `None` until full decompression; `.Z` has no size trailer (best-effort truncation via nonzero leftover bits) |
| XZ, ZST | Header size when encoder wrote it; otherwise `None` |
| LZ4 | Frame content-size field when present; otherwise `None` |
| LZIP | Available from the trailer on a seekable source |
| LZMA Alone | 8-byte Alone header size when not the unknown marker (`0xFFFFFFFFFFFFFFFF`); otherwise `None` |

Availability of an index/trailer-derived size SHALL be decided by **the source's shape**,
never by the caller's declared member-stream capability. `seekable_members` /
`open_stream(seekable=…)` declare intent to `seek()` inside a member stream; they select
an indexed decompressor backend and resolve accelerator `AUTO`, and they MUST NOT change
what metadata a member reports. A seekable source SHALL yield the same `member.size` with
and without the declaration; a non-seekable source SHALL yield `None` for every
index/trailer-derived size, and no probe SHALL force a decompression pass to obtain one.

When a decoder learns the true uncompressed size after EOF, the member MAY be
updated to that byte count.

#### Scenario: size matrix

| Case | Expected |
| --- | --- |
| `.gz` opened | Single member size is `None` |
| `.bz2` before full decompression | Size is `None` |
| `.bz2` fully read to EOF | Size may update to actual uncompressed byte count |
| `.lz` opened from a seekable source | Size is available from the trailer |
| `.xz` / `.lz`, seekable source, with and without `seekable_members=True` | Same `member.size` both ways |
| `.xz` / `.lz` from a pipe | Size is `None`; no decode pass is forced |
| Alone stream with known header size | `member.size` equals that size |
| Alone stream with unknown-size marker | Size is `None` until EOF may update it |
| Truncated `.Z` with nonzero leftover bits | Available bytes delivered; next `read()` raises `TruncatedError` |

### Requirement: Surface stored decompressed digests without decompression

The single-file backend SHALL surface a codec's stored (or cheaply derived-from-stored)
decompressed-content digest(s) on `member.hashes` when readable without decompressing,
and SHALL omit them otherwise. This serves cheap dedupe (`VISION.md` "hashes without
decompression") and never triggers a decompression pass.

Keys and value types follow the public `HashAlgorithm` / `bytes` contract (api-coherence
hashes typing). Surfacing SHALL NOT change read behavior: a full read still verifies via
the existing path; stored/derived values are metadata only.

Whether a digest is cheaply readable SHALL depend only on the codec and **the source's
shape**, never on the caller's declared member-stream capability — the founding dedupe
caller does a plain `open_archive()` and never asks to `seek()`.

- **GZIP:** trailer `CRC32` only when exactly one member and the source is seekable/path.
  Multi-member → omit (trailer covers only the last member; mid-member trailers are not
  cheap without decompress).
- **LZIP:** on a seekable source, surface `CRC32` of the whole synthetic member from the
  lzip index. For multi-member files, the value SHALL equal
  `crc32(concat(member payloads))` derived by combining per-trailer CRC-32 values with
  each member's exact uncompressed `data_size` (combine algebra). Single-member
  degenerates to the trailer CRC.
- **Non-seekable source:** omit digests that require a trailer/index peek (no forced
  decode).
- **BZ2, XZ, ZLIB, BR, `.Z`:** no cheap whole-member stored digest — omit. (Zlib's
  RFC 1950 Adler-32 trailer is verified by the decompressor on read; it is not surfaced
  on `member.hashes` because the wrapper has no size fields for a reliable
  single-stream boundary when concat/trailing junk is possible.)

#### Scenario: stored-digest surfacing by codec

| Case | `member.hashes` |
| --- | --- |
| Single-member `.gz`, seekable/path | `CRC32` present |
| Multi-member `.gz` | no digest key |
| `.gz` non-seekable | no digest key |
| Single-member `.lz`, seekable source | `CRC32` present (= trailer) |
| Multi-member `.lz`, seekable source | `CRC32` present (= combine of per-member trailers) |
| `.lz` seekable, with and without `seekable_members=True` | Same `hashes` both ways |
| `.lz` from a pipe | no digest key |
| `.bz2` / `.xz` / `.zlib` / `.br` / `.Z` | no digest key |
| Any of the above, full `read()` | verification unchanged; hashes are metadata only |
