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
| zstd | `zstandard` | optional `[zstd]` |
| lz4 | `lz4` | optional `[lz4]` |
| PPMd (var.H) | `pyppmd` | optional `[7z]` |
| Deflate64 | `inflate64` | optional `[7z]` |
| AES-256 (decrypt stage) | the wrapped crypto backend | optional `[crypto]` |

#### Scenario: default gzip backend

- **WHEN** a gzip stream is opened with default configuration
- **THEN** it is decompressed using the stdlib `gzip` module

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
non-seekable source as the documented non-seekable error. No raw backend exception
escapes unwrapped.

#### Scenario: corrupt compressed data

- **WHEN** a corrupted compressed stream is read
- **THEN** `CorruptionError` is raised with the original exception attached as `__cause__`

#### Scenario: truncated compressed data

- **WHEN** a compressed stream ends mid-data
- **THEN** `TruncatedError` is raised

---

### Requirement: Verify decompressed output against expected digests

The system SHALL provide a composable verification **stage** that wraps a
sequential decompressed stream and, given the expected digests from a member's
`hashes`, computes each algorithm incrementally as bytes are read and verifies the
results when the stream reaches **clean end-of-stream**, raising `CorruptionError`
(naming the algorithm) on mismatch. Because expected digests come from
already-parsed member metadata, the stage is agnostic to whether the source format
stored them before or after the data.

This stage is distinct from any integrity check a codec performs internally (e.g.
the gzip trailer CRC or the xz stream check, which the codec backend already
surfaces as `CorruptionError`/`TruncatedError`); it verifies the *container-supplied*
digest over the decompressed bytes.

Constraints:

- Verification SHALL run **only on a full sequential read to clean EOF**. A stream
  closed after a partial read SHALL NOT verify or raise, because the digest of
  partial content is undefined. The stage therefore applies to the sequential read
  path, not to random-access reads served by `seekable-decompressor-streams`.
- **The mismatch SHALL be raised from the read that signals end-of-stream — after all
  decompressed bytes have already been delivered to the caller.** The stage MUST NOT
  withhold or discard the final data chunk: a consumer using the canonical
  `while chunk := f.read(n): ...` loop receives **every** byte first, and then the
  terminal read (the call that would otherwise return `b""`/EOF) raises
  `CorruptionError` instead. This guarantees the caller never loses a trailing chunk of
  data that may well be correct; the integrity verdict is delivered *after* the data,
  not in place of it. The bytes-returning `read()`-all path (and `reader.read(member)`)
  internally reads to EOF, so it raises on mismatch and returns no bytes — an
  all-or-nothing read of data that failed integrity cannot be handed back as valid.
  (A consumer that stops early, or reads exactly `size` bytes without probing EOF, falls
  under the partial-read rule above and is not verified.)
- For a digest algorithm the stage cannot compute — because its backend is not
  installed (e.g. `blake2sp` without the `[rar]` Blake2sp backend) or it is an
  unknown algorithm — it SHALL emit a warning via the `archivey` integrity logger
  and skip that algorithm rather than failing the read; algorithms it can compute
  (always including CRC32, via stdlib) are still verified. This is the verification
  counterpart of the decode rule: a missing *decode* backend raises (the bytes
  cannot be produced), but a missing *hash* backend only skips (the bytes are fine,
  just unverified).

#### Scenario: digest mismatch on full read

- **WHEN** a member is read to EOF and its decompressed bytes do not match `hashes["crc32"]`
- **THEN** `CorruptionError` naming the algorithm is raised

#### Scenario: mismatch surfaces at EOF without losing the final chunk

- **WHEN** a consumer reads a member with `while chunk := f.read(n)` and the content's digest does not match
- **THEN** every data chunk (including the last) is delivered normally, and the **following** read — the one that would signal EOF — raises `CorruptionError`; no trailing data is withheld

#### Scenario: partial read is not verified

- **WHEN** a consumer reads only the first part of a member's stream and abandons it
- **THEN** no verification occurs and no error is raised

#### Scenario: unverifiable algorithm is skipped with a warning

- **WHEN** a member's only expected digest is `blake2sp` and the Blake2sp backend is not installed
- **THEN** the stage logs an integrity warning and returns the data without raising

---

### Requirement: Backend dispatch is separable from opening

The system SHALL allow the open function and its exception translator for a given
codec/configuration to be resolved independently of opening a stream, so callers
(format detection, the TAR reader, the 7z folder pipeline) can reuse the correct
backend.

#### Scenario: resolve a backend without opening

- **WHEN** the open function for a codec and configuration is requested
- **THEN** the function and its matching exception translator are returned
