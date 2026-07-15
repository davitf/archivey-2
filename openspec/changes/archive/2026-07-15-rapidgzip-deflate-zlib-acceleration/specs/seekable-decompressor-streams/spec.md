## ADDED Requirements

### Requirement: DEFLATE-family random access uses rapidgzip

The system SHALL use `rapidgzip` (the `[seekable]` accelerator, `>=0.16.0`) as the optional
random-access/parallel backend for raw DEFLATE (`deflate` codec) and zlib-wrapped DEFLATE
(`zlib` codec), in addition to gzip. rapidgzip auto-detects `GZIP`/`ZLIB`/`DEFLATE`, so the
codec SHALL pass the stream through unwrapped — no synthetic gzip header/footer. Selection is
gated identically to gzip (`use_rapidgzip` × declared seekability × availability, plus the
`AUTO` minimum-input-size threshold) and the raw accelerator SHALL be wrapped in the same
close-on-finalize guard. The default sequential backend is unchanged: when rapidgzip is
unavailable, `OFF`, or below the `AUTO` threshold, deflate/zlib decode through stdlib `zlib`.

rapidgzip over-reads past a DEFLATE end-of-stream looking for a concatenated member, so the
codec SHALL feed it an exactly-bounded input (e.g. the container's `SlicingStream` sized to
the member's compressed length); an unbounded or over-long stream MAY raise a spurious
"Invalid deflate block" error on the trailing bytes.

#### Scenario: deflate/zlib accelerator matrix

| Case | Expected |
| --- | --- |
| Declared-seekable raw deflate, `use_rapidgzip` enabled, size ≥ threshold | rapidgzip decodes/seeks without full re-decompression; input passed unwrapped |
| Declared-seekable zlib stream, accelerator enabled | rapidgzip auto-detects ZLIB and decodes; backward seek without re-decompress from start |
| rapidgzip absent, `OFF`, or size < `AUTO` threshold | stdlib `zlib` (`-15` / `MAX_WBITS`); backward seek re-decompresses from start |
| Accelerator fed an over-long/unbounded slice | May raise a spurious decode error on trailing bytes; callers MUST bound the input |

## MODIFIED Requirements

### Requirement: Seek machinery is demand-driven

The system SHALL construct seek support only when seekability is declared:
`MemberStreams.SEEKABLE` on `open_archive()` or `seekable=True` on the
single-stream API. Undeclared streams SHALL NOT parse XZ footers, scan lzip
trailers, instantiate rapidgzip accelerators, retain rewind buffers, or retain
seek-point tables; they are forward-only.

`use_rapidgzip` and `use_indexed_bzip2` SHALL resolve their `AUTO` / `ON` / `OFF`
configuration against declared seek demand, not the `streaming` access-mode
proxy. For declared-seekable streams, native XZ/lzip indexes, rapidgzip-backed
gzip/bzip2 indexes, and stdlib fallback rewinds retain their contracts. The
stdlib O(n)-per-rewind path MAY serve the seek but MUST emit the documented
slow-rewind diagnostic/warning.

Under `AUTO`, the system SHALL additionally require the known compressed input size to reach a
documented minimum threshold before selecting rapidgzip for any DEFLATE-family stream (deflate,
zlib, gzip), so per-stream accelerator setup is not paid for members too small to benefit. The
threshold value is fixed by benchmark and recorded in design. When the input size is not known
in advance, `AUTO` SHALL behave as it did before this threshold existed (select the accelerator
when otherwise eligible). `ON` ignores the threshold; `OFF` never selects rapidgzip.

#### Scenario: demand matrix

| Case | Expected |
| --- | --- |
| gzip/xz/bzip2/lzip opened without seekability under `AUTO` | No index, no accelerator, forward-only |
| Same stream opened with seekability, `AUTO`, accelerator installed, size ≥ threshold | Accelerator or native index provides random access |
| Declared-seekable deflate/zlib/gzip under `AUTO`, known size < threshold | rapidgzip not selected; stdlib backend used |
| Declared-seekable DEFLATE-family stream, `use_rapidgzip=ON`, size below threshold | rapidgzip still selected (threshold ignored) |
| Declared-seekable gzip without accelerator, caller seeks backward | Seek re-decompresses from start and warns/names `[seekable]` accelerator |

### Requirement: Accelerator errors translate uniformly

The system SHALL translate corrupt/truncated input from rapidgzip-backed gzip,
bzip2, deflate, and zlib into the same `compressed-streams` errors as stdlib paths:
`CorruptionError` or `TruncatedError`, never raw third-party exceptions. This
translator SHALL account for platform-varying rapidgzip exception types/messages.

For seekable-source gzip through rapidgzip, the system SHALL backstop truncation
by comparing full-read decompressed length modulo 2^32 with the gzip ISIZE
trailer. A conservative multi-member scan SHALL prevent valid concatenated gzip
streams from being misreported when the trailer records only the last member.

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
| Truncated gzip through rapidgzip from seekable source | `TruncatedError` via ISIZE backstop or `CorruptionError` from accelerator; never silent short read |
| Truncated standalone deflate/zlib through rapidgzip | Corruption in a block → `CorruptionError`; a clean mid-stream cut MAY return a short read undetected (no checksum backstop) |
| Truncated/corrupt container DEFLATE member (e.g. ZIP) | Container CRC mismatch → `CorruptionError`/`TruncatedError` via the verifying stage |
| Valid concatenated multi-member gzip | Decompresses fully without false truncation |

### Requirement: Index-less rewinds emit diagnostic data

When an index-less codec first services a backward seek by re-decompressing from
the start, the system SHALL emit `STREAM_REWIND_REDECOMPRESSES` with codec,
before/after offsets, and accelerator name or `None`. It SHALL emit at most once
per stream. Forward/no-op seeks SHALL emit nothing.

The event SHALL live on the stream operation and cumulative owning-reader
aggregate, never on `CostReceipt` or `ArchiveInfo`. Gzip/bzip2/deflate/zlib context names the
`[seekable]` accelerator when a rapidgzip path was eligible; brotli, lz4, and zstd record no
accelerator. XZ, lzip, and unix-compress indexed seeks SHALL NOT emit this event.
Stdlib zstd SHALL rewind in place like other index-less codecs. When a deflate/zlib stream
falls back to stdlib `zlib` (accelerator absent, `OFF`, or below the `AUTO` threshold), its
rewind SHALL name the `[seekable]` accelerator, consistent with the gzip fallback.

#### Scenario: slow rewind matrix

| Case | Expected |
| --- | --- |
| One index-less stream performs many backward seeks | One `STREAM_REWIND_REDECOMPRESSES` occurrence; later rewinds no duplicate |
| Rewind diagnostic resolves to `RAISE` | `DiagnosticRaisedError` is raised from that seek |
| Only forward/no-op seeks occur | No rewind occurrence |
| zstd stream rewinds via stdlib backend | Re-decompresses from start in place and emits one occurrence |
| Stdlib-fallback zlib/deflate stream rewinds | Emits one occurrence naming the `[seekable]` accelerator |
