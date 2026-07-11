# Compressed Streams

## Purpose

The compressed-streams layer is the single place where Archivey turns compressed
or encrypted bytes into a decompressed pull stream. It owns one decompressor-stream
abstraction, a registry of per-codec backends (stdlib and optional packages), an
AES decrypt stage, uniform exception translation, and missing-dependency reporting.
**Format parsers compose this layer rather than calling codec libraries directly**,
so a codec is implemented once and reused across the single-file compressors, the
native 7z reader, and a future native ZIP reader. The seekable-index features live
in the separate `seekable-decompressor-streams` capability, which builds on this one.

## Requirements

### Requirement: Format parsers decompress only through the shared stream layer

The system SHALL expose codec decompression through a uniform pull-based
decompressor-stream interface (an `open_stream(...)`-style entry point returning a
`BinaryIO`). Format backends — single-file compressors, the native 7z reader, and
future readers such as a native ZIP reader — SHALL obtain decompression by composing
these shared stream backends and MUST NOT import or drive codec libraries
(`pyppmd`, `inflate64`, `lzma` raw filters, the crypto backend, etc.) directly.

#### Scenario: 7z reader composes shared codec streams

- **WHEN** the native 7z reader decodes a folder coded as, e.g., Delta + LZMA2
- **THEN** it builds the pipeline from the shared decompressor-stream backends rather than calling `lzma` itself

#### Scenario: a codec is implemented once and reused

- **WHEN** both the 7z reader and a future ZIP reader need Deflate64
- **THEN** both use the same shared `inflate64`-backed stream backend, not a format-local copy

---

### Requirement: open_stream is non-seekable by default

The single-stream entry point (`open_stream(...)`-style API) SHALL return a
forward-only stream by default and SHALL accept `seekable: bool = False` to request a
seekable stream. This matches the archive-side rule — no archivey stream is seekable
unless asked — so the seek contract is learned once and applies everywhere.
Concurrency is not a concept for this API (it returns exactly one stream), so it takes
the boolean, not the `MemberStreams` flags enum.

Without `seekable=True`: the returned stream reports `seekable() is False`, `seek()`
raises `io.UnsupportedOperation`, `tell()` works, and no seek index or accelerator is
instantiated. With `seekable=True`: the `seekable-decompressor-streams` contract applies
(native indexes, demand-driven accelerator `AUTO` resolution, loud slow rewinds on the
non-accelerated path).

#### Scenario: default stream is forward-only

- **WHEN** a compressed source is opened through the single-stream API without
  `seekable=True`
- **THEN** the stream reads correctly forward, reports `seekable() is False`, raises
  `io.UnsupportedOperation` on `seek()`, and builds no seek index

#### Scenario: requested seekability activates the seekable-stream contract

- **WHEN** the same source is opened with `seekable=True`
- **THEN** the stream is seekable per `seekable-decompressor-streams`, using native
  indexes or accelerators where available and warning loudly on O(n) rewind fallbacks

---

### Requirement: A codec is described by one StreamCodec descriptor

The system SHALL represent each single-stream codec as a single descriptor object that
carries its open function, exception translator, exact magic signatures, an optional
**content-probe function** (for a format with no exact magic, that inspects a peeked prefix
and returns whether it matches), its standalone file extensions, an optional metadata
extractor that fills `ArchiveMember` fields, and its optional-dependency requirement
(package / extra / external tool + install hint + unlocked capability). A codec SHALL be
recognized by EITHER an exact magic signature OR a content-probe function, not a separate
"weak magic" flag. A new standalone codec SHALL become fully readable and detectable by
registering one descriptor, without edits to the detector, the single-file reader, or the
registry's availability code. The descriptor registry MUST NOT eagerly import optional
codec libraries, so the zero-dep core stays importable with no third-party packages.

#### Scenario: adding a standalone codec is a one-descriptor change

- **WHEN** a new single-stream codec descriptor is registered (open fn, translator, magic/probe, extension, requirement)
- **THEN** `detect_format()` recognizes it, `SingleFileBackend` reads it as a one-member archive, and `format_availability()` reports its support — with no other code changes

#### Scenario: descriptors do not pull in optional libraries at import

- **WHEN** `archivey` is imported in a core-only environment (no optional codec packages)
- **THEN** building the descriptor registry raises no `ImportError` and imports no third-party codec package

---

### Requirement: Each codec has a default backend

The system SHALL decompress each supported codec through a default backend:

| Codec | Default backend | Availability |
|-------|-----------------|--------------|
| gzip | stdlib `gzip` | core |
| bzip2 | stdlib `bz2` | core |
| xz | native xz stream over stdlib `lzma` | core |
| LZMA1 / LZMA2 (raw) | stdlib `lzma` `FORMAT_RAW` | core |
| Delta, BCJ x86/ARM/ARMT/PPC/SPARC/IA64 | `lzma` raw filters | core |
| raw Deflate | stdlib `zlib` (`-15`) | core |
| Copy/STORED | pass-through | core |
| zstd | stdlib `compression.zstd` (3.14+) / `backports.zstd` (<3.14) | optional `[zstd]` on <3.14; core on 3.14+ |
| lz4 | `lz4` | optional `[lz4]` |
| Brotli | `brotli` | optional `[7z]` |
| unix-compress (LZW, `.Z`) | `uncompresspy` | optional `[unix-compress]` |
| PPMd (var.H) | `pyppmd` | optional `[7z]` |
| Deflate64 | `inflate64` | optional `[7z]` |
| AES-256 (decrypt stage) | the wrapped crypto backend | optional `[crypto]` |

#### Scenario: default gzip backend

- **WHEN** a gzip stream is opened with default configuration
- **THEN** it is decompressed using the stdlib `gzip` module

#### Scenario: default zstd backend on Python 3.14+

- **WHEN** a zstd stream is opened with default configuration on Python 3.14 or newer
- **THEN** it is decompressed using the standard-library `compression.zstd` module

#### Scenario: default zstd backend on Python 3.11–3.13

- **WHEN** a zstd stream is opened with default configuration on Python 3.11–3.13 and `backports.zstd` is installed
- **THEN** it is decompressed using `backports.zstd` (the same `compression.zstd` API)

#### Scenario: raw LZMA2 backend for a 7z folder

- **WHEN** a 7z folder's LZMA2 stream is opened
- **THEN** it is decompressed using `lzma` in `FORMAT_RAW` mode

---

### Requirement: A single, wrapped crypto backend provides the AES stage

The system SHALL standardize on the `cryptography` package as the one crypto
backend (resolved by the `[crypto]` extra), accessed only through an internal
abstraction so the backend can be swapped without touching format parsers. AES
decryption is exposed as a decrypt **stage** that composes ahead of a decompressor
in a stream pipeline (e.g. AES → LZMA2 for an encrypted 7z folder).

#### Scenario: encrypted folder composes a decrypt stage before decompression

- **WHEN** a 7z folder is AES-encrypted over LZMA2 and `[crypto]` is installed
- **THEN** the pipeline applies the AES decrypt stage, then the LZMA2 decompressor

#### Scenario: crypto backend is reachable only through the wrapper

- **WHEN** any format parser needs AES
- **THEN** it uses the internal crypto abstraction, not `cryptography` directly, so the backend is swappable

---

### Requirement: Missing optional backends raise PackageNotInstalledError

The system SHALL raise `PackageNotInstalledError`, naming the missing package, when
a codec's selected backend requires an optional package that is not installed —
rather than failing obscurely.

#### Scenario: PPMd without pyppmd

- **WHEN** a PPMd stream is opened and `pyppmd` is not installed
- **THEN** `PackageNotInstalledError` naming `pyppmd` is raised

#### Scenario: AES without the crypto backend

- **WHEN** an AES-encrypted stream is opened and `[crypto]` is not installed
- **THEN** `PackageNotInstalledError` naming the crypto backend is raised

---

### Requirement: Returned streams translate decompression errors

The system SHALL wrap each backend stream so decompression failures surface as the
library's own exception types: corrupt data as `CorruptionError`, unexpected
end-of-input as `TruncatedError`, and a backend that requires seeking on a
non-seekable source as the documented non-seekable error. For the zstd backend
specifically, the `compression.zstd` `ZstdError` SHALL map to `CorruptionError`
and its `EOFError` (raised on a truncated frame) SHALL map to `TruncatedError`.
No raw backend exception escapes unwrapped.

#### Scenario: corrupt compressed data

- **WHEN** a corrupted compressed stream is read
- **THEN** `CorruptionError` is raised with the original exception attached as `__cause__`

#### Scenario: truncated compressed data

- **WHEN** a compressed stream ends mid-data
- **THEN** `TruncatedError` is raised

#### Scenario: truncated zstd data raises

- **WHEN** a zstd stream that ends before its end-of-frame marker is read to EOF
- **THEN** `TruncatedError` is raised (the stdlib backend reports the cut as `EOFError`), rather than a silent short read

#### Scenario: corrupt zstd data raises

- **WHEN** a zstd frame carrying a content checksum is corrupted and read
- **THEN** `CorruptionError` is raised with the backend `ZstdError` attached as `__cause__`

---

### Requirement: Verify decompressed output against expected digests

The verification stage SHALL continue to raise `CorruptionError` for a computable digest
mismatch at clean EOF and skip verification after a partial read. It SHALL compute each
available algorithm incrementally over decompressed bytes. A mismatch SHALL surface from
the terminal read that would otherwise signal EOF, after every data chunk has been
delivered; a bytes-returning full read SHALL raise and return no bytes. Codec-internal
checks remain distinct from this container-supplied digest stage, and random-access/
partial reads do not verify.

When an expected digest algorithm cannot be computed because it is unknown or its backend
is unavailable, the stage SHALL emit `DIGEST_UNVERIFIABLE` with typed context containing
the algorithm, non-secret reason, and member identity when available.

The event SHALL follow diagnostic policy. Under `COLLECT`, that algorithm is skipped while
other computable algorithms are still verified; the occurrence appears in the stream/
reader aggregate and MAY attach to the member under the shared retention budget. Under
`IGNORE`, it is counted but has no delivery/detail and verification still skips it. Under
`RAISE`, `DiagnosticRaisedError` halts the read.

#### Scenario: unverifiable digest is collected as data

- **WHEN** a member's expected `blake2sp` cannot be computed and default policy applies
- **THEN** `DIGEST_UNVERIFIABLE` is counted/retained/logged, may attach to the member, and the readable bytes are returned without that digest check

#### Scenario: digest mismatch on full read

- **WHEN** a member is read to EOF and its decompressed bytes do not match an expected computable digest
- **THEN** `CorruptionError` naming the algorithm is raised

#### Scenario: mismatch does not discard the final chunk

- **WHEN** a caller consumes chunks until EOF from a member whose digest mismatches
- **THEN** every data chunk is delivered, and the following terminal read raises `CorruptionError`

#### Scenario: partial read is not verified

- **WHEN** a caller abandons a member stream before clean EOF
- **THEN** no digest verdict or mismatch exception is produced

#### Scenario: strict caller escalates unverifiable digest

- **WHEN** `DIGEST_UNVERIFIABLE` resolves to `RAISE`
- **THEN** `DiagnosticRaisedError` halts opening/reading the stream rather than silently skipping the check

### Requirement: Public ArchiveStream exposes bounded operation snapshots

Every public `ArchiveStream` SHALL expose an immutable `diagnostics` snapshot. For a
reader-owned stream this is an operation-filtered view over the reader collector; for a
standalone codec stream it is a stream-lifetime collector. Serving the view SHALL not
retain a second aggregate copy of each occurrence.

#### Scenario: standalone stream owns its diagnostics

- **WHEN** a standalone codec stream emits an index or rewind diagnostic
- **THEN** `stream.diagnostics` exposes exact counts and bounded retained details without requiring an `ArchiveReader`

### Requirement: Read-only stream wrappers share an internal base; the public surface is an ArchiveStream

The read-only stream wrappers in this layer SHALL share an internal base that provides the
read-only `BinaryIO` surface (`readable`/`writable`/`write`) and a single canonical
`readinto`/`readall` built on each wrapper's `read`, so that primitive is defined once rather
than re-implemented per wrapper. The object returned on the public/codec-stream path SHALL
always be an `ArchiveStream` — the single, stable surface where stream-level presentation
metadata (e.g. a rewind-is-slow warning, and future seek-cost information) lives; transient
internal opens (`backend.open()`) MAY return the raw backend stream without this wrapper.

#### Scenario: read-only wrappers expose a uniform read-only surface

- **WHEN** any read-only stream wrapper in this layer is used
- **THEN** its `readable()`/`writable()`/`write()` and `readinto`/`readall` come from the shared base, defined once rather than re-implemented per wrapper

#### Scenario: the public codec stream is an ArchiveStream

- **WHEN** a codec stream is opened on the public path
- **THEN** the returned object is an `ArchiveStream` carrying any stream-level presentation metadata (such as a rewind-is-slow warning)

---

### Requirement: Backend dispatch is separable from opening

The system SHALL allow the open function and its exception translator for a given
codec/configuration to be resolved independently of opening a stream, so callers
(format detection, the TAR reader, the 7z folder pipeline) can reuse the correct
backend.

#### Scenario: resolve a backend without opening

- **WHEN** the open function for a codec and configuration is requested
- **THEN** the function and its matching exception translator are returned

---

### Requirement: Decompression streams expose compressed bytes consumed

The decompression stream layer SHALL expose a monotonically increasing count of the number of
**compressed bytes consumed from the underlying source** so far, so a caller can compute a live
decompression ratio without knowing the source's total size. The count is surfaced as a running
value (e.g. an `input_bytes_consumed` property) backed by a counting reader wrapping the raw
compressed source; it is incremented as the decompressor pulls input and is available even when
the source is a non-seekable pipe.

The reader SHALL surface the running total for the archive's outer compressed source (parallel
to the cheap-total `compressed_source_size`) as `compressed_bytes_consumed`, returning `None`
when there is no single compressed source to count (an uncompressed container, a directory).
When a member stream is served from the same outer compressed stream (solid / streamed
containers), that member stream's consumption is reflected in the same outer counter — the count
is cumulative across the archive, not reset per member.

The counter SHALL be cheap (an integer incremented on `read()`), and reporting it SHALL NOT
change what bytes are read or decompressed.

#### Scenario: consumed count grows as a compressed stream is read

- **WHEN** a compressed stream (e.g. a `.gz`) is read incrementally from a non-seekable source
- **THEN** the exposed compressed-bytes-consumed count increases monotonically toward the total
  input, and is readable at any point mid-stream

#### Scenario: no counter for an uncompressed or non-stream source

- **WHEN** the archive has no single compressed source (an uncompressed container or a directory)
- **THEN** `compressed_bytes_consumed` is `None`

#### Scenario: reporting the count does not perturb decoding

- **WHEN** the compressed-bytes-consumed count is read repeatedly during extraction
- **THEN** the decompressed output is byte-for-byte identical to reading without observing the count
