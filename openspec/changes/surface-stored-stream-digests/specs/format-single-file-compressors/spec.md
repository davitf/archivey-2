## RENAMED Requirements

- FROM: `### Requirement: Surface the stored decompressed CRC without decompression`
- TO: `### Requirement: Surface stored decompressed digests without decompression`

## MODIFIED Requirements

### Requirement: Surface stored decompressed digests without decompression

The single-file backend SHALL surface a codec's stored (or cheaply derived-from-stored)
decompressed-content digest(s) on `member.hashes` when readable without decompressing,
and SHALL omit them otherwise. This serves cheap dedupe (`VISION.md` "hashes without
decompression") and never triggers a decompression pass.

Keys and value types follow the public `HashAlgorithm` / `bytes` contract (api-coherence
hashes typing). Surfacing SHALL NOT change read behavior: a full read still verifies via
the existing path; stored/derived values are metadata only.

- **GZIP:** trailer `CRC32` only when exactly one member and the source is seekable/path.
  Multi-member → omit (trailer covers only the last member; mid-member trailers are not
  cheap without decompress).
- **LZIP:** when the seekable lzip index is available, surface `CRC32` of the whole
  synthetic member. For multi-member files, the value SHALL equal
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
| Single-member `.lz`, seekable lzip index | `CRC32` present (= trailer) |
| Multi-member `.lz`, seekable lzip index | `CRC32` present (= combine of per-member trailers) |
| `.lz` without seekable index | no digest key |
| `.bz2` / `.xz` / `.zlib` / `.br` / `.Z` | no digest key |
| Any of the above, full `read()` | verification unchanged; hashes are metadata only |
