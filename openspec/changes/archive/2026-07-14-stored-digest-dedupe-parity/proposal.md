## Why

The founding use case is *index-and-deduplicate decades of backups*, and `VISION.md`
lists "hashes without decompression where possible" as core: a dedupe pass should use
`member.hashes` without reading data. Coverage is currently uneven and undocumented.
7z populates `crc32`, ZIP now populates `crc32` (central directory), RAR5 populates
`crc32`/`blake2sp` — but **single-file gzip and lzip do not surface their stored
decompressed-CRC trailer** even though it is cheaply readable from a seekable/path
source (the deep-review roadmap reply confirms both formats store a CRC-32 of the
decompressed content). There is also no written policy for *which backend surfaces
which stored digest* and no conformance-sweep assertion, so parity silently drifts.
This is cheap, additive, and directly serves the reason the library exists — and the
digest fields are far more credible shipped complete than backfilled after users notice
the holes.

## What Changes

- **Single-file gzip:** surface the trailer CRC-32 as `member.hashes["crc32"]` when it is
  cheaply readable (seekable/path source), without decompressing. Multi-member gzip: the
  trailer CRC covers only the last member — surface only when a single member is present
  (the same caveat the gzip truncation backstop already handles), otherwise omit.
- **Single-file lzip:** surface the per-member trailer CRC-32 as `member.hashes["crc32"]`
  via the seekable lzip backend (the trailer is already read there for size).
- **Documented cross-backend policy:** add a `documentation` requirement + user-facing
  recipe stating which backends surface which stored digests, the "best available digest
  + provenance (stored vs computed)" recommendation, and the cheap-dedupe recipe.
- **Conformance-sweep parity assertion:** the corpus sweep asserts every backend surfaces
  its documented stored digest(s) for applicable members (and omits them where none is
  stored), so parity is regression-gated rather than incidental.

## Capabilities

### New Capabilities

<!-- none -->

### Modified Capabilities

- `format-single-file-compressors`: surface gzip/lzip decompressed-CRC trailer as
  `member.hashes["crc32"]` without decompression; single-member gzip caveat.
- `testing-contract`: conformance-sweep asserts stored-digest parity across backends.
- `documentation`: document the stored-digest matrix + cheap-dedupe recipe.

## Impact

- Backends: `single_file_reader.py` (gzip/lzip trailer peek — reuse the existing
  `MetadataContext`/trailer-read hook that already yields size).
- No change to ZIP/7z/RAR (already compliant) beyond the sweep now asserting them.
- Public surface: `member.hashes["crc32"]` newly populated on single-member gzip/lzip;
  additive (empty→populated). No behavior change to reads or verification.
- Docs: new stored-digest matrix in the end-user guide.
