## MODIFIED Requirements

### Requirement: Document the stored-digest matrix and cheap-dedupe recipe

The end-user documentation SHALL include a stored-digest matrix stating, per format, which
`member.hashes` keys are populated from data the archive already stores (or, for
multi-member lzip, derived via CRC combine from per-member stored CRCs) without
decompression, and under what conditions (e.g. single-member/seekable gzip; seekable
lzip including multi-member). It SHALL include a cheap-dedupe recipe showing how to key
on `member.hashes` for a first-pass dedupe and fall back to computing a digest while
reading when no stored digest is present, and SHALL state the "best available digest +
provenance (stored vs computed)" recommendation so an indexer can choose cheap-but-weak
vs costly-but-strong uniformly. The matrix SHALL list zlib with formats that have no
cheap whole-member stored digest on `member.hashes` (Adler-32 remains decompressor-checked).

#### Scenario: stored-digest documentation

| Case | Expected |
| --- | --- |
| Reader consults the guide for dedupe | Finds the per-format stored-digest matrix and the cheap→computed fallback recipe |
| Format stores no cheap digest (e.g. bzip2, zlib, tar) | Matrix states so; recipe covers the computed-on-read fallback |
| Multi-member lzip | Matrix lists combined `crc32` |
