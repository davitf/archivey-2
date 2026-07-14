## ADDED Requirements

### Requirement: Decode ZIP member bodies through the shared codec layer

The ZIP backend SHALL decode member data through the shared `compressed-streams` codec
layer rather than stdlib `zipfile`'s internal decoders. It SHALL locate a member's raw
compressed bytes via a bounded local-file-header parse (fixed header + local name/extra
lengths, with the same absurd-length rejection discipline the native parsers apply) and a
slice over the source, then dispatch by ZIP method id to the codec's default backend.
Central-directory parsing and listing MAY continue to use stdlib `zipfile`.

Extended codecs SHALL be supported when their backing extra is installed: DEFLATE64
(method 9), ZSTD (method 93), PPMD (method 98), alongside STORED/DEFLATE/BZIP2/LZMA. A
member whose codec backend is not installed SHALL raise `PackageNotInstalledError` (as
other backends do), not stdlib `zipfile`'s `NotImplementedError`. A corrupt member body
SHALL raise `CorruptionError` via the shared translation. Member reads SHALL verify
`member.hashes["crc32"]` through the shared `VerifyingStream`.

Encrypted members (ZipCrypto / WinZip AE) SHALL retain their current decryption path;
only unencrypted members route through the codec layer in this change. Non-seekable ZIP
sources remain rejected (unchanged).

#### Scenario: ZIP codec-layer decoding

| Case | Expected |
| --- | --- |
| STORED / DEFLATE / BZIP2 / LZMA member, unencrypted | Decodes via the shared codec layer; CRC verified through `VerifyingStream` |
| DEFLATE64 (method 9) member, `inflate64` backend present | Decodes (stdlib `zipfile` cannot); absent backend → `PackageNotInstalledError` |
| ZSTD (method 93) / PPMD (method 98) member, backend present | Decodes; absent backend → `PackageNotInstalledError` |
| Unsupported/unknown method id | Documented unsupported-codec error; no guessed output |
| Corrupt member body | `CorruptionError` |
| Encrypted (ZipCrypto / AE) member | Decrypts via the existing path; behavior unchanged |
| Deflate64/Zstd/PPMd corpus entry | Opens/lists/reads in the conformance sweep (skip if producer/oracle absent) |
