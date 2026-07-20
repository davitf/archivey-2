## MODIFIED Requirements

### Requirement: Each supported codec has a default backend

The system SHALL decompress supported codecs through these default backends:

| Codec | Default backend | Availability |
| --- | --- | --- |
| gzip | `DecompressorStream` + gzip-window `zlib` decoder (`wbits=16+MAX_WBITS`), multi-member chaining with GzipFile parity | core |
| bzip2 | stdlib `bz2` | core |
| xz | native xz stream over stdlib `lzma` | core |
| LZMA Alone | stdlib `lzma` `FORMAT_ALONE` | core |
| LZMA1 / LZMA2 raw | stdlib `lzma` `FORMAT_RAW` | core |
| Delta, BCJ x86/ARM/ARMT/PPC/SPARC/IA64 | `lzma` raw filters | core |
| raw Deflate | stdlib `zlib` (`-15`) via `DecompressorStream` | core |
| Copy/STORED | pass-through | core |
| zstd | stdlib `compression.zstd` (3.14+) / `backports.zstd` (<3.14) | optional `[zstd]` before 3.14; core on 3.14+ |
| lz4 | `lz4` | optional `[lz4]` |
| Brotli | `brotli` | optional `[7z]` |
| unix-compress `.Z` | native LZW `DecompressorStream` | core |
| PPMd var.H | `pyppmd` | optional `[7z]` |
| Deflate64 | `inflate64` | optional `[7z]` |
| AES-256 decrypt stage | wrapped crypto backend | optional `[crypto]` |

LZMA Alone SHALL be a distinct stream-codec descriptor from raw LZMA1/LZMA2
(`FORMAT_RAW` + properties). Alone is standalone (`StreamFormat.LZMA_ALONE`);
raw LZMA1/LZMA2 remain container-only.

The gzip stdlib path MUST NOT use `gzip.GzipFile` / `gzip.open` as the decode
engine. CRC/ISIZE check **outcomes** SHALL remain equivalent to stdlib gzip
(implemented via zlib’s gzip window). Concatenated multi-member gzip SHALL
decompress to the concatenation of member payloads with GzipFile parity: skip
zero padding between members; trailing zeros only end the stream cleanly;
trailing non-gzip junk after a completed member SHALL raise `CorruptionError`.
Optional rapidgzip acceleration remains gated by `seekable-decompressor-streams`
and is unchanged by this requirement.

#### Scenario: backend matrix

| Case | Expected |
| --- | --- |
| Default gzip stream (accelerator off / ineligible) | gzip-window `DecompressorStream`; not `GzipFile` |
| Concatenated multi-member `.gz` | Full concatenated payload |
| Multi-member `.gz` with zero padding between members | Full concatenated payload |
| Valid member then trailing zeros only | Clean EOF after payload |
| Valid member then trailing non-gzip junk | `CorruptionError` |
| Default zstd on Python 3.14+ | stdlib `compression.zstd` |
| Default zstd on Python 3.11-3.13 with `backports.zstd` | `backports.zstd` using the same API |
| Standalone `.lzma` / Alone stream | `lzma` in `FORMAT_ALONE` mode |
| 7z folder LZMA2 raw stream | `lzma` in `FORMAT_RAW` mode |
| Default unix-compress `.Z` stream | native LZW stream; no `uncompresspy` import |
| Core-only install opens `.Z` | Succeeds without optional extras |

### Requirement: Returned streams translate decompression errors

The system SHALL wrap backend streams so decompression failures surface as
Archivey exceptions: corrupt data as `CorruptionError`, unexpected end-of-input
as `TruncatedError`, and source seek requirements as the documented non-seekable
error. No raw backend exception SHALL escape. For zstd specifically,
`compression.zstd.ZstdError` SHALL map to `CorruptionError`, and its truncation
`EOFError` SHALL map to `TruncatedError`.

When a `DecompressorStream`-backed codec reaches compressed EOF without
`finished` (truncated deflate/gzip/zlib/unix-compress and similar), the stream
SHALL deliver any recoverable decompressed prefix already produced for a bounded
`read(n)` (including flush leftover and bytes already buffered for that call)
before surfacing `TruncatedError`. The error SHALL be deferred via
`pending_error` and raised on the next empty `read`. `readall` / `read(-1)`
SHALL raise `TruncatedError` when incomplete EOF is discovered (it MUST NOT
return a prefix from that call). Large bounded `read(n)` SHALL recover the same
prefix as a `read(1)` loop (no byte-at-a-time requirement). Silent success on
truncated input remains forbidden: incomplete EOF MUST NOT publish a clean
complete decompressed size, and `seek(SEEK_END)` / size queries MUST NOT treat
the recoverable prefix as a successful full stream.

#### Scenario: decompression error matrix

| Case | Expected |
| --- | --- |
| Corrupt compressed stream is read | `CorruptionError` with backend exception as `__cause__` |
| Compressed stream ends mid-data; bounded `read(n)` | Recoverable prefix delivered; `TruncatedError` on next empty `read` |
| Truncated gzip via stdlib path; `read(65536)` | Correct prefix returned; `TruncatedError` on next empty `read` |
| Truncated gzip; `read(1)` loop | Same prefix length/content as large-read path; then `TruncatedError` |
| Truncated gzip; `readall` / `read(-1)` | `TruncatedError` (no prefix returned from that call) |
| Truncated stream; `seek(SEEK_END)` / `try_get_size` after incomplete EOF | `TruncatedError` or size remains unknown — never a silent prefix-as-complete size, never bare `AssertionError` |
| Zstd stream ends before end-of-frame marker | `TruncatedError`, not a silent short read |
| Zstd checksum frame is corrupted | `CorruptionError` with backend `ZstdError` as `__cause__` |

### Requirement: Content faults raise from read, never from close

Decode and verify streams SHALL raise content `TruncatedError` and
`CorruptionError` from `read` / `readall` (and from size/seek paths that would
otherwise report a false clean completion). `close()` MUST NOT raise those
content faults. `close()` MAY still propagate teardown failures (`OSError`,
translated inner-close errors). A caller that reads a partial prefix with
bounded `read(n)` and closes without a follow-up empty `read` MAY miss a
deferred `pending_error` — that abandon gap is accepted; the implementation
MUST still refuse silent success via size/`SEEK_END`. Deliberate partial read
then close before clean EOF remains quiet for digest/length verification
(abandon before verdict), matching prior verify semantics.

`VerifyingStream` / fused `MemberVerifier` SHALL raise short-of-expected-size
and digest-mismatch verdicts from the read path (`readall` or the terminal empty
`read`), not from `finish_on_close`. `finish_on_close` SHALL close the inner and
MUST NOT introduce a first content `TruncatedError` / `CorruptionError` solely
because the caller is closing.

#### Scenario: close vs read matrix

| Case | Expected |
| --- | --- |
| Truncated `DecompressorStream`; catch on empty `read`; then `close()` | `close()` succeeds |
| Truncated gzip stdlib path; error already observed on `read`; then `close()` | `close()` succeeds |
| Hash-less short member; `read()`/`readall` consumed all available bytes | `TruncatedError` from that read path (not from `close`) |
| Digest mismatch; full sequential read | `CorruptionError` from `readall` or terminal empty `read` (not from `close`) |
| Partial read then `close` before clean EOF (verify) | No digest/length verdict |
| Inner teardown fails on `close` | Teardown error may propagate |

### Requirement: Decompressed output digests are verified at clean EOF

The verification stage SHALL compute available expected digest algorithms
incrementally over decompressed bytes and raise `CorruptionError` for a
computable mismatch at clean EOF. A mismatch SHALL surface from the terminal
`read` after all data chunks have been delivered; a bytes-returning full read
(`readall` / `read(-1)`) that reaches clean EOF with a mismatch SHALL raise and
return no bytes. Partial/random-access reads SHALL NOT produce a digest verdict.
`close()` MUST NOT be the sole surface for a digest or short-length verdict.

Supported computable algorithms SHALL include `crc32` (via `zlib.crc32`),
`adler32` (via `zlib.adler32`), the `hashlib.algorithms_available` set, and
`blake2sp` (the 8-way parallel BLAKE2s tree hash used by RAR5), computed via an
internal zero-dependency hasher. A well-formed member carrying only a `blake2sp`
digest SHALL therefore be verified, not skipped. When an expected `adler32` is
installed on a verifying stream, it SHALL likewise be computed and checked (not
skipped as unknown).

When an expected digest cannot be computed because the algorithm is genuinely unknown
or a backend is missing, the system SHALL emit `DIGEST_UNVERIFIABLE` with algorithm,
non-secret reason, and member identity when available. Diagnostic policy controls
collection, logging/callback delivery, member attachment, and escalation.

#### Scenario: digest matrix

| Case | Expected |
| --- | --- |
| Expected `blake2sp` on a well-formed RAR5 member | Computed and verified; mismatch raises `CorruptionError` |
| Expected `adler32` on a verifying stream | Computed and verified; mismatch raises `CorruptionError` |
| Expected digest under a genuinely-unknown algorithm name | `DIGEST_UNVERIFIABLE` counted/retained/logged; bytes still returned without that check |
| Full member read reaches EOF with computable digest mismatch | `CorruptionError` naming the algorithm from the read path |
| Chunked read reaches EOF with mismatch | All valid chunks delivered; following terminal empty `read` raises |
| Single `read()`/`readall` returns all bytes then would need a verdict | Verdict on that call or an immediate follow-up empty `read` — not deferred solely to `close` |
| Caller abandons stream before clean EOF | No digest verdict or mismatch exception on `close` |
| Unverifiable digest resolves to `RAISE` | `DiagnosticRaisedError` halts open/read |

### Requirement: Read-only stream wrappers share one internal base

Read-only wrappers in this layer SHALL share an internal base for the read-only
`BinaryIO` surface (`readable`, `writable`, `write`) and canonical `readinto` /
`readall` built from each wrapper's `read`. The public codec-stream path SHALL
return an `ArchiveStream` carrying stream-level presentation metadata; internal
`backend.open()` calls MAY return raw backend streams.

The seekable decompressor path SHALL be a single concrete stream class
(`DecompressorStream`) parameterized by a `Decoder` strategy, not a per-codec
subclass hierarchy. Every codec — forward-only and segmented alike — SHALL plug in
through **one** decoder protocol, which also owns seek-index discovery:

```python
@dataclass
class DecodeOut:
    data: bytes
    points: list[SeekPoint]  # absolute; empty for forward-only codecs

class Decoder(Protocol):
    def recreate(self, point: SeekPoint, inner: BinaryIO) -> Decoder: ...
    def feed(self, chunk: bytes) -> DecodeOut: ...
    def flush(self) -> DecodeOut: ...
    @property
    def finished(self) -> bool: ...
    @property
    def pending_error(self) -> BaseException | None: ...
    def clear_pending_error(self) -> None: ...
    # Default no-op; only index-bearing codecs (xz, lzip, future BGZF) override it.
    def build_index(
        self, inner: BinaryIO, last_known: SeekPoint
    ) -> tuple[list[SeekPoint], int | None]: ...
```

The stream — not the decoder — SHALL own the buffer, position, seek-point table,
and seek algorithm; it SHALL be format-agnostic, storing whatever `SeekPoint`s a
decoder emits. The `Decoder` SHALL choose seek-point placement (member/stream start
vs. post-realignment) and MAY perform progressive index enrichment during `feed`
using the `inner` it retained from `recreate`, restoring `inner`'s position itself.
Forward-only codecs SHALL emit empty `points` and inherit the no-op `build_index`.
Deferred truncation (unix-compress leftover bits; incomplete zlib/gzip/deflate EOF)
SHALL surface through `pending_error` after delivering recoverable bytes on bounded
`read(n)`; the stream SHALL raise it on the next empty `read`, and SHALL clear it
via `clear_pending_error` after raising (and on seek reset). `readall` / `read(-1)`
SHALL raise when `pending_error` is set after draining rather than returning a
prefix. `close()` SHALL NOT raise `pending_error`. Incomplete EOF SHALL NOT
publish a clean complete `_size`. Adding a codec SHALL add a `Decoder` and MUST NOT
require a new stream subclass or a `SegmentedDecompressorStream` layer.

#### Scenario: wrapper surface matrix

| Case | Expected |
| --- | --- |
| Any read-only stream wrapper is used | Shared base supplies read-only surface and `readinto` / `readall` |
| Public codec stream is opened | Returned object is an `ArchiveStream` with stream presentation metadata |

#### Scenario: decoder composition matrix

| Case | Expected |
| --- | --- |
| Forward-only codec (zlib/deflate, brotli, ppmd, bcj, deflate64) | Implements `recreate`/`feed`/`flush`/`finished`; empty `points`; no-op `build_index` |
| Gzip stdlib decoder | Same protocol; chains concatenated members with GzipFile parity (NUL pad / junk / trailing zeros) |
| Segmented boundary codec (lzip, xz stream start) | `feed` emits a `SeekPoint` at the boundary; stream stores it |
| Progressive enrichment (xz block index) | `feed` scans footer via retained `inner`; restores position |
| One-shot / forward walk (xz, lzip; future BGZF) | `build_index` returns points + size; demand-driven per seekable spec |
| Deferred truncation (unix-compress; truncated zlib/gzip/deflate) | Bounded `read(n)` delivers prefix; `pending_error`; raise on next empty `read`; not on `close` |
| `readall` on truncated stream | Raises `TruncatedError` |
| A new codec is added | One `Decoder`; no new stream subclass |
