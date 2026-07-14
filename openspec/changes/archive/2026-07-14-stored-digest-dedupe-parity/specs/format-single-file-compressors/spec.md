## ADDED Requirements

### Requirement: Surface the stored decompressed CRC without decompression

The single-file backend SHALL surface a codec's stored decompressed-content CRC-32 as
`member.hashes["crc32"]` when it is cheaply readable from the source without
decompressing, and SHALL omit it otherwise. This serves cheap dedupe (`VISION.md`
"hashes without decompression") and never triggers a decompression pass.

- **GZIP:** the 8-byte trailer's CRC-32 SHALL be surfaced only when the stream contains
  exactly one member and the source is seekable/path (peek the trailer). For concatenated
  multi-member gzip the trailer CRC covers only the last member, so `crc32` SHALL be
  omitted. Reuse the member-count detection already used by the truncation backstop.
- **LZIP:** the per-member trailer CRC-32 SHALL be surfaced via the seekable lzip backend
  (which already reads the trailer for size); omitted when that backend/source is
  unavailable.
- **Non-seekable source:** `crc32` SHALL be omitted (no forced decode); callers compute it
  while reading.
- **BZ2, XZ, ZLIB, BR, `.Z`:** no cheap whole-member stored CRC — `crc32` SHALL be absent.

Surfacing the stored CRC SHALL NOT change read behavior or verification: a full read still
verifies via the existing path, and the stored value is metadata only.

#### Scenario: stored CRC surfacing by codec

| Case | `member.hashes["crc32"]` |
| --- | --- |
| Single-member `.gz`, seekable/path source | Present (trailer CRC-32) |
| Multi-member `.gz` | Absent (trailer covers only last member) |
| `.gz` on a non-seekable source | Absent (no forced decode) |
| `.lz` via seekable lzip backend | Present (per-member trailer CRC-32) |
| `.bz2` / `.xz` / `.zlib` / `.br` / `.Z` | Absent (no cheap whole-member stored CRC) |
| Any of the above, full `read()` | Verification unchanged; stored value is metadata only |
