# seekable-decompressor-streams — gzip truncation backstop for any seekable source

## MODIFIED Requirements

### Requirement: Accelerator errors translate uniformly

The system SHALL translate corrupt/truncated input from rapidgzip-backed gzip,
bzip2, deflate, and zlib into the same `compressed-streams` errors as stdlib paths:
`CorruptionError` or `TruncatedError`, never raw third-party exceptions. This
translator SHALL account for platform-varying rapidgzip exception types/messages.

For gzip through rapidgzip, the system SHALL backstop truncation by comparing full-read
decompressed length modulo 2^32 with the gzip ISIZE trailer, for **any declared-seekable
source** — a path or a caller-owned `BinaryIO` alike — not only path sources. The ISIZE trailer
value SHALL be captured up front (when the source is first inspected for backstop eligibility),
so no per-read reopen of a path is required and a non-path source needs no seek while the
accelerator is live. Where rapidgzip reaches EOF having delivered zero bytes, the system SHALL
rewind the seekable source and re-decode through the stdlib gzip engine so recoverable prefixes
stream and truncation still raises from a read (never `close()`). A conservative multi-member
scan SHALL prevent valid concatenated gzip streams from being misreported when the trailer
records only the last member.

A **caller-owned** source driven through the accelerator SHALL NOT be closed by the accelerator
or its truncation wrapper (archivey never closes a source the caller owns); the accelerator's
close-on-finalize guard closes only archivey-owned handles. rapidgzip's `terminate()`-on-raising-
source hazard on Python file objects SHALL be contained so a source-side fault surfaces as a
translated `compressed-streams` error rather than aborting the process.

rapidgzip does not validate zlib's Adler-32 and returns a silent short read on some
mid-stream DEFLATE truncations, and raw DEFLATE carries no checksum, so there is no
ISIZE-equivalent truncation backstop for the deflate/zlib accelerator path. A DEFLATE-family
member decoded inside a container (e.g. a ZIP member) SHALL rely on the container's own
checksum (CRC-32 via the shared verifying stage) to catch truncation/corruption. A standalone
zlib/deflate stream accelerated by rapidgzip MAY therefore miss a truncation that stdlib `zlib`
would report; this is an accepted limitation of the accelerator path (tracked with the gzip
truncation work), and corruption inside a DEFLATE block SHALL still surface as `CorruptionError`.

#### Scenario: accelerator error matrix

| Case | Expected |
| --- | --- |
| Corrupt gzip/bzip2/deflate/zlib through rapidgzip | `CorruptionError`; raw accelerator exception never escapes |
| Truncated gzip through rapidgzip from a seekable **path** | `TruncatedError` via ISIZE backstop / empty→stdlib, or `CorruptionError` from accelerator; never silent short read |
| Truncated gzip through rapidgzip from a seekable **non-path** `BinaryIO` | Same as the path case — backstop active; caller source left open afterward |
| Caller-owned source after the archivey stream closes | Still open and readable; accelerator/wrapper closed only its own view |
| Truncated standalone deflate/zlib through rapidgzip | Corruption in a block → `CorruptionError`; a clean mid-stream cut MAY return a short read undetected (no checksum backstop) |
| Truncated/corrupt container DEFLATE member (e.g. ZIP) | Container CRC mismatch → `CorruptionError`/`TruncatedError` via the verifying stage |
| Valid concatenated multi-member gzip | Decompresses fully without false truncation |
