## MODIFIED Requirements

### Requirement: Each supported codec has a default backend

The system SHALL decompress supported codecs through these default backends:

| Codec | Default backend | Availability |
| --- | --- | --- |
| gzip | `DecompressorStream` + gzip-window `zlib` decoder (`wbits=16+MAX_WBITS`), multi-member chaining | core |
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
engine (CRC/trailer checks remain via zlib’s gzip window). Concatenated
multi-member gzip SHALL decompress to the concatenation of member payloads
(GzipFile parity). Optional rapidgzip acceleration remains gated by
`seekable-decompressor-streams` and is unchanged by this requirement.

#### Scenario: backend matrix

| Case | Expected |
| --- | --- |
| Default gzip stream (accelerator off / ineligible) | gzip-window `DecompressorStream`; not `GzipFile` |
| Concatenated multi-member `.gz` | Full concatenated payload |
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
SHALL deliver any recoverable decompressed prefix (including flush leftover and
bytes already buffered for the current `read`) before surfacing
`TruncatedError`. The error SHALL be deferred via `pending_error` and raised on
the next empty `read` or on `close` — it MUST NOT raise in a way that drops a
prefix the decoder already produced. Large `read(n)` / `read(-1)` SHALL recover
the same prefix as a `read(1)` loop (no byte-at-a-time requirement). Silent
success on truncated input remains forbidden.

#### Scenario: decompression error matrix

| Case | Expected |
| --- | --- |
| Corrupt compressed stream is read | `CorruptionError` with backend exception as `__cause__` |
| Compressed stream ends mid-data | Recoverable prefix delivered; then `TruncatedError` |
| Truncated gzip via stdlib path; `read(65536)` or `read(-1)` | Correct prefix returned; `TruncatedError` on next empty read or `close` |
| Truncated gzip; `read(1)` loop | Same prefix length/content as large-read path; then `TruncatedError` |
| Zstd stream ends before end-of-frame marker | `TruncatedError`, not a silent short read |
| Zstd checksum frame is corrupted | `CorruptionError` with backend `ZstdError` as `__cause__` |

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
SHALL surface through `pending_error` after delivering recoverable bytes; the stream
SHALL raise it on the next empty `read` or on `close`, and SHALL clear it via
`clear_pending_error` after raising (and on seek reset). `readall` / `read(-1)` SHALL
return the recoverable prefix and leave `pending_error` set rather than dropping
bytes to raise immediately. Adding a codec SHALL add a `Decoder` and MUST NOT
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
| Gzip stdlib decoder | Same protocol; chains concatenated members via `unused_data` |
| Segmented boundary codec (lzip, xz stream start) | `feed` emits a `SeekPoint` at the boundary; stream stores it |
| Progressive enrichment (xz block index) | `feed` scans footer via retained `inner`; restores position |
| One-shot / forward walk (xz, lzip; future BGZF) | `build_index` returns points + size; demand-driven per seekable spec |
| Deferred truncation (unix-compress; truncated zlib/gzip/deflate) | Deliver prefix; `pending_error`; raise on next empty `read` or `close` |
| `readall` on truncated stream | Returns prefix; `pending_error` remains for empty read / `close` |
| A new codec is added | One `Decoder`; no new stream subclass |
