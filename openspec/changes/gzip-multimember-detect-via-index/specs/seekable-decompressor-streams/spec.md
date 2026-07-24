# seekable-decompressor-streams — multi-member gzip detection via rapidgzip's index

## MODIFIED Requirements

### Requirement: Accelerator errors translate uniformly

The system SHALL translate corrupt/truncated input from rapidgzip-backed gzip,
bzip2, deflate, and zlib into the same `compressed-streams` errors as stdlib paths:
`CorruptionError` or `TruncatedError`, never raw third-party exceptions. This
translator SHALL account for platform-varying rapidgzip exception types/messages.

For seekable-source gzip through rapidgzip, the system SHALL backstop truncation
by comparing full-read decompressed length modulo 2^32 with the gzip ISIZE
trailer. When that comparison mismatches, the system SHALL disambiguate a valid
concatenated multi-member gzip (whose trailer records only the last member) from a
real truncation by consulting **rapidgzip's already-built index** for gzip member
boundaries — not by a second full-file scan for a further gzip header. The index is
authoritative for this decision because the check only guards against false-flagging a
**valid** file, and a valid file's index is complete. Where rapidgzip does not expose member
boundaries, or the index is unavailable, the system SHALL fall back to the conservative
byte scan. In all cases the disambiguation SHALL preserve the no-false-positive direction: it
MAY miss a truncation but SHALL NEVER misreport a valid concatenated gzip as truncated.

rapidgzip does not validate zlib's Adler-32 and returns a silent short read on some
mid-stream DEFLATE truncations, and raw DEFLATE carries no checksum, so there is no
ISIZE-equivalent truncation backstop for the deflate/zlib accelerator path. A DEFLATE-family
member decoded inside a container (e.g. a ZIP member) SHALL rely on the container's own
checksum (CRC-32 via the shared verifying stage) to catch truncation/corruption. A standalone
zlib/deflate stream accelerated by rapidgzip MAY therefore miss a truncation that stdlib `zlib`
would report; this is an accepted limitation of the accelerator path (tracked with the gzip
truncation work), and corruption inside a DEFLATE block SHALL still surface as `CorruptionError`.

#### Scenario: multi-member disambiguation uses the index

- **WHEN** an accelerated gzip read to EOF yields an ISIZE mismatch and rapidgzip's index exposes gzip member boundaries
- **THEN** the system decides "valid multi-member (do not raise)" vs "truncated (raise `TruncatedError`)" from the index member count, performing no second full-file scan

#### Scenario: fallback when the index cannot answer

- **WHEN** rapidgzip does not expose member boundaries or the index is unavailable
- **THEN** the system falls back to the conservative byte scan (any further `1f 8b 08` ⇒ do not raise), never false-flagging a valid concatenated gzip

#### Scenario: accelerator error matrix

| Case | Expected |
| --- | --- |
| Corrupt gzip/bzip2/deflate/zlib through rapidgzip | `CorruptionError`; raw accelerator exception never escapes |
| Truncated gzip through rapidgzip from seekable source | `TruncatedError` via ISIZE backstop or `CorruptionError` from accelerator; never silent short read |
| Valid concatenated multi-member gzip | Decompresses fully without false truncation; disambiguated via the index, no second full-file scan |
| Truncated standalone deflate/zlib through rapidgzip | Corruption in a block → `CorruptionError`; a clean mid-stream cut MAY return a short read undetected (no checksum backstop) |
| Truncated/corrupt container DEFLATE member (e.g. ZIP) | Container CRC mismatch → `CorruptionError`/`TruncatedError` via the verifying stage |
