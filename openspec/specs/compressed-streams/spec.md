# Compressed Streams

## Purpose

Compressed streams are the shared pull-stream layer that turns compressed or
encrypted bytes into decompressed bytes. Format parsers compose this layer rather
than calling codec libraries directly, so codecs, AES decryption, exception
translation, dependency checks, digest verification, diagnostics, and compressed
byte accounting are implemented once.

## Related specs

| Spec | Relationship |
| --- | --- |
| `archive-data-model` | `CompressionMethod`, member hashes, and standalone raw-stream formats |
| `seekable-decompressor-streams` | Seekable/indexed behavior when seekability is requested |
| `error-handling` | Typed exception hierarchy and cause preservation |
| `diagnostics` | Digest, rewind, and seek-index diagnostic policy/retention |
| `backend-registry` | Codec availability and install hints for format support |

## Requirements

### Requirement: Format parsers use the shared decompressor-stream layer

The system SHALL expose codec decompression through one pull-based
`open_stream(...)`-style API returning `BinaryIO`/`ArchiveStream`. Single-file
compressors, native 7z, and future native ZIP SHALL compose shared stream
backends and MUST NOT directly import or drive codec libraries such as `pyppmd`,
`inflate64`, raw `lzma` filters, or the crypto backend.

#### Scenario: shared pipeline matrix

| Case | Expected |
| --- | --- |
| Native 7z decodes Delta + LZMA2 | Builds pipeline from shared stream backends |
| 7z and future ZIP need Deflate64 | Both use the same `inflate64`-backed stream backend |

### Requirement: open_stream is forward-only unless seekability is requested

The single-stream API SHALL default to a forward-only stream and accept
`seekable: bool = False`. Without `seekable=True`, the stream reports
`seekable() is False`, `seek()` raises `io.UnsupportedOperation`, `tell()` works,
and no seek index or accelerator is instantiated. With `seekable=True`, the
`seekable-decompressor-streams` contract SHALL apply. Concurrency is not a
parameter because the API returns one stream.

#### Scenario: seekability matrix

| Case | Expected |
| --- | --- |
| Open compressed source without `seekable=True` | Reads forward; `seekable()` false; `seek()` unsupported; no index |
| Open same source with `seekable=True` | Seekable behavior follows `seekable-decompressor-streams` |

### Requirement: One StreamCodec descriptor describes each codec

The system SHALL register each single-stream codec through one descriptor
containing its open function, exception translator, exact magic signatures,
optional content probe, file extensions, metadata extractor, and optional
dependency requirement (package/extra/tool, install hint, unlocked capability).
A codec SHALL be recognized by exact magic or content probe; there is no separate
weak-magic flag. Descriptor construction MUST NOT eagerly import optional codec
libraries.

Registering a standalone codec descriptor SHALL make detection, the single-file
reader, and availability reporting work without edits elsewhere.

#### Scenario: descriptor matrix

| Case | Expected |
| --- | --- |
| New standalone codec descriptor is registered | `detect_format()`, `SingleFileBackend`, and availability reporting pick it up |
| Import `archivey` with no optional codec packages | No third-party codec import and no `ImportError` |

### Requirement: Each supported codec has a default backend

The system SHALL decompress supported codecs through these default backends:

| Codec | Default backend | Availability |
| --- | --- | --- |
| gzip | stdlib `gzip` | core |
| bzip2 | stdlib `bz2` | core |
| xz | native xz stream over stdlib `lzma` | core |
| LZMA Alone | stdlib `lzma` `FORMAT_ALONE` | core |
| LZMA1 / LZMA2 raw | stdlib `lzma` `FORMAT_RAW` | core |
| Delta, BCJ x86/ARM/ARMT/PPC/SPARC/IA64 | `lzma` raw filters | core |
| raw Deflate | stdlib `zlib` (`-15`) | core |
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

#### Scenario: backend matrix

| Case | Expected |
| --- | --- |
| Default gzip stream | stdlib `gzip` |
| Default zstd on Python 3.14+ | stdlib `compression.zstd` |
| Default zstd on Python 3.11-3.13 with `backports.zstd` | `backports.zstd` using the same API |
| Standalone `.lzma` / Alone stream | `lzma` in `FORMAT_ALONE` mode |
| 7z folder LZMA2 raw stream | `lzma` in `FORMAT_RAW` mode |
| Default unix-compress `.Z` stream | native LZW stream; no `uncompresspy` import |
| Core-only install opens `.Z` | Succeeds without optional extras |

### Requirement: AES decryption is one wrapped pipeline stage

The system SHALL use `cryptography` from `[crypto]` through an internal wrapper
only. AES decryption SHALL be a stream stage composed before decompression, such
as AES then LZMA2 for an encrypted 7z folder. Format parsers MUST use the wrapper
instead of importing `cryptography` directly.

#### Scenario: crypto matrix

| Case | Expected |
| --- | --- |
| AES-encrypted 7z folder over LZMA2 with `[crypto]` installed | Pipeline applies AES decrypt stage, then LZMA2 |
| Any format parser needs AES | Uses internal crypto abstraction |

### Requirement: Missing optional backends raise PackageNotInstalledError

The system SHALL raise `PackageNotInstalledError`, naming the missing package,
extra, or tool, when the selected codec/decrypt backend requires an unavailable
optional component.

#### Scenario: missing backend matrix

| Case | Expected |
| --- | --- |
| PPMd stream without `pyppmd` | `PackageNotInstalledError` naming `pyppmd` |
| AES stream without `[crypto]` | `PackageNotInstalledError` naming the crypto backend |

### Requirement: Returned streams translate decompression errors

The system SHALL wrap backend streams so decompression failures surface as
Archivey exceptions: corrupt data as `CorruptionError`, unexpected end-of-input
as `TruncatedError`, and source seek requirements as the documented non-seekable
error. No raw backend exception SHALL escape. For zstd specifically,
`compression.zstd.ZstdError` SHALL map to `CorruptionError`, and its truncation
`EOFError` SHALL map to `TruncatedError`.

#### Scenario: decompression error matrix

| Case | Expected |
| --- | --- |
| Corrupt compressed stream is read | `CorruptionError` with backend exception as `__cause__` |
| Compressed stream ends mid-data | `TruncatedError` |
| Zstd stream ends before end-of-frame marker | `TruncatedError`, not a silent short read |
| Zstd checksum frame is corrupted | `CorruptionError` with backend `ZstdError` as `__cause__` |

### Requirement: Decompressed output digests are verified at clean EOF

The verification stage SHALL compute available expected digest algorithms
incrementally over decompressed bytes and raise `CorruptionError` for a
computable mismatch at clean EOF. A mismatch SHALL surface from the terminal read
after all data chunks have been delivered; a bytes-returning full read raises and
returns no bytes. Partial/random-access reads SHALL NOT produce a digest verdict.

When an expected digest cannot be computed because the algorithm is unknown or a
backend is missing, the system SHALL emit `DIGEST_UNVERIFIABLE` with algorithm,
non-secret reason, and member identity when available. Diagnostic policy controls
collection, logging/callback delivery, member attachment, and escalation.

#### Scenario: digest matrix

| Case | Expected |
| --- | --- |
| Expected `blake2sp` cannot be computed under default policy | `DIGEST_UNVERIFIABLE` counted/retained/logged; bytes still returned without that check |
| Full member read reaches EOF with computable digest mismatch | `CorruptionError` naming the algorithm |
| Chunked read reaches EOF with mismatch | All valid chunks delivered; following terminal read raises |
| Caller abandons stream before clean EOF | No digest verdict or mismatch exception |
| `DIGEST_UNVERIFIABLE` resolves to `RAISE` | `DiagnosticRaisedError` halts open/read |

### Requirement: Public ArchiveStream exposes bounded operation diagnostics

Every public `ArchiveStream` SHALL expose an immutable `diagnostics` snapshot. A
reader-owned stream shows an operation-filtered view over the reader collector; a
standalone codec stream owns a stream-lifetime collector. Serving the view SHALL
not retain another aggregate copy of each occurrence.

#### Scenario: ArchiveStream diagnostics matrix

| Case | Expected |
| --- | --- |
| Standalone codec stream emits index/rewind diagnostic | `stream.diagnostics` exposes exact counts and bounded details without a reader |
| Reader-owned member stream emits diagnostic | Stream view and reader aggregate share one retained occurrence |

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
Forward-only codecs SHALL emit empty `points`, keep `pending_error` `None`, and
inherit the no-op `build_index`. Deferred truncation (e.g. unix-compress leftover
bits) SHALL surface through `pending_error`, raised on the next empty `read` after
delivering bytes; the stream SHALL clear it via `clear_pending_error` after raising
(and on seek reset). Adding a codec SHALL add a `Decoder` and MUST NOT require a new
stream subclass or a `SegmentedDecompressorStream` layer.

#### Scenario: wrapper surface matrix

| Case | Expected |
| --- | --- |
| Any read-only stream wrapper is used | Shared base supplies read-only surface and `readinto` / `readall` |
| Public codec stream is opened | Returned object is an `ArchiveStream` with stream presentation metadata |

#### Scenario: decoder composition matrix

| Case | Expected |
| --- | --- |
| Forward-only codec (zlib, brotli, ppmd, bcj, deflate64) | Implements `recreate`/`feed`/`flush`/`finished`; emits empty `points`; `pending_error` `None`; inherits no-op `build_index` |
| Segmented boundary codec (lzip, xz stream start) | `feed` emits a `SeekPoint` at the boundary with the codec's own before/after placement; stream stores it |
| Progressive enrichment (xz block index) | `feed` scans the completed stream's footer via retained `inner` and emits block `SeekPoint`s (carrying resume `state`); restores `inner` position |
| One-shot / forward walk (xz, lzip backward scan; future BGZF forward walk) | `build_index` returns points + size; stream drives it demand-driven per `seekable-decompressor-streams` |
| Deferred truncation (unix-compress leftover bits) | `pending_error` set after `flush`; base raises it on the next empty `read` |
| A new codec is added | One `Decoder` added; no new stream subclass; no `SegmentedDecompressorStream` layer |

### Requirement: Backend dispatch is separable from opening

The system SHALL allow callers to resolve a codec/configuration's open function
and matching exception translator independently of opening a stream, so detection,
TAR, and 7z folder pipelines reuse the same backend selection.

#### Scenario: backend dispatch matrix

| Case | Expected |
| --- | --- |
| Open function is requested for a codec/configuration | Function and matching exception translator are returned |

### Requirement: Decompression streams count compressed bytes consumed

The decompression layer SHALL expose a monotonically increasing count of
compressed bytes consumed from the underlying source, such as
`input_bytes_consumed`. The counter SHALL be cheap, available for non-seekable
pipes, and MUST NOT perturb bytes read or decompressed.

Archive readers SHALL surface the running total for a single outer compressed
source as `compressed_bytes_consumed`, returning `None` when no single compressed
source exists (uncompressed container, directory). When solid/streamed member
streams share that outer source, the count is cumulative across the archive.

#### Scenario: compressed-byte counter matrix

| Case | Expected |
| --- | --- |
| `.gz` read incrementally from non-seekable source | Count increases monotonically and is readable mid-stream |
| Uncompressed container or directory | `compressed_bytes_consumed is None` |
| Count is observed repeatedly during extraction | Decompressed output is byte-for-byte unchanged |
