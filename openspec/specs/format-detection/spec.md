# Format Detection

## Purpose

Archivey can identify the archive format of a source (file path or binary stream) without fully opening it. Detection returns a `FormatInfo` dataclass carrying the detected format, a confidence score, and an encoding hint. Detection never consumes or discards bytes from the source.

## Requirements

### Requirement: detect_format() returns a FormatInfo

The system SHALL expose a top-level function with the following signature:

```python
archivey.detect_format(
    source: str | Path | BinaryIO,
) -> FormatInfo
```

The return value SHALL be a frozen dataclass:

```python
@dataclass(frozen=True)
class FormatInfo:
    format: ArchiveFormat
    confidence: float               # 0.0–1.0; magic match = 1.0; extension-only = 0.3
    detected_by: str                # "magic", "extension", "content_probe"
    encoding_hint: str | None       # suggested encoding for legacy path fields
    payload_offset: int = 0         # byte offset of the archive payload; nonzero for
                                    # SFX archives behind an executable stub
```

#### Scenario: magic byte match

- **WHEN** the source's leading bytes match a known magic pattern
- **THEN** `detect_format()` returns a `FormatInfo` with `confidence=1.0` and `detected_by="magic"`

#### Scenario: extension-only fallback

- **WHEN** the source is a `Path` and no magic byte match is found
- **THEN** `detect_format()` returns a `FormatInfo` with `confidence=0.3` and `detected_by="extension"`

---

### Requirement: Magic-first detection with extension fallback and confidence scoring

The system SHALL execute format detection using the following algorithm:

1. Read up to `DETECTION_LIMIT` bytes (default 4 096 bytes) from the source.
2. Match the bytes against the magic-byte table (exact offsets, no heuristics).
3. On a match: return `FormatInfo(confidence=1.0, detected_by="magic")`.
4. On no match: attempt extension-based guess if source is a `Path`; return `confidence=0.3, detected_by="extension"`.

#### Scenario: unrecognised bytes with no path available

- **WHEN** a non-seekable `BinaryIO` with no associated filename is supplied and the peeked bytes match no magic pattern
- **THEN** `detect_format()` raises `FormatDetectionError`

---

### Requirement: Conflict resolution — magic wins and warning is emitted

The system SHALL prefer the magic-byte result over the file-extension result whenever they disagree. When such a conflict occurs the system SHALL emit a `logging.WARNING` via the `archivey.detection` logger.

#### Scenario: mismatched extension and magic

- **WHEN** a file named `archive.tar.gz` opens with the 7-Zip magic header `37 7A BC AF 27 1C`
- **THEN** `detect_format()` returns `FormatInfo(format=ArchiveFormat.SEVEN_Z, confidence=1.0, detected_by="magic")`
- **AND** a `logging.WARNING` is emitted noting the conflict between magic and extension

---

### Requirement: Magic-byte table

The system SHALL recognise the following formats by inspecting bytes at the specified offsets. All matches are exact; no fuzzy or heuristic matching is performed.

| Format | Offset | Magic bytes |
|--------|--------|-------------|
| ZIP (standard/data descriptor/empty) | 0 | `50 4B 03 04` / `50 4B 07 08` / `50 4B 05 06` |
| GZip | 0 | `1F 8B` |
| BZip2 | 0 | `42 5A 68` |
| XZ | 0 | `FD 37 7A 58 5A 00` |
| Zstandard | 0 | `28 B5 2F FD` |
| 7-Zip | 0 | `37 7A BC AF 27 1C` |
| RAR 4.x | 0 | `52 61 72 21 1A 07 00` |
| RAR 5.x | 0 | `52 61 72 21 1A 07 01 00` |
| ISO 9660 | 32 769 | `43 44 30 30 31` ("CD001") — requires ≥ 32 774 bytes peek |
| TAR (POSIX/GNU) | 257 | `75 73 74 61 72` ("ustar") — requires ≥ 512 bytes peek |
| LZ4 | 0 | `04 22 4D 18` |

#### Scenario: ZIP standard local file header

- **WHEN** the source begins with bytes `50 4B 03 04`
- **THEN** `detect_format()` returns `FormatInfo(format=ArchiveFormat.ZIP, confidence=1.0, detected_by="magic")`

#### Scenario: TAR with ustar signature

- **WHEN** the source has bytes `75 73 74 61 72` at offset 257 and the stream is at least 512 bytes long
- **THEN** `detect_format()` returns `FormatInfo(format=ArchiveFormat.TAR, confidence=1.0, detected_by="magic")`

---

### Requirement: Compressed streams are probed for an inner TAR

When the outer stream is a single-file compressor (gzip, bzip2, xz, zstd, lz4),
detection SHALL peek a bounded prefix of the *decompressed* content and test for the
TAR `ustar` signature at offset 257, so a tarball is reported as the combined format
(`TAR_GZ` / `TAR_BZ2` / `TAR_XZ` / `TAR_ZST` / `TAR_LZ4`) rather than a bare
single-file compressor. The probe SHALL decompress only enough to reach the TAR
header region (≥ 512 decompressed bytes). If the compressor's decompression backend
is unavailable, detection reports the bare compressor format and defers the inner-TAR
determination to open time.

#### Scenario: gzip wrapping a tar

- **WHEN** a `.gz` stream decompresses to bytes carrying `ustar` at offset 257
- **THEN** `detect_format()` returns `ArchiveFormat.TAR_GZ` (not bare `GZIP`)

#### Scenario: gzip wrapping a single file

- **WHEN** a `.gz` stream decompresses to content with no TAR signature
- **THEN** `detect_format()` returns `ArchiveFormat.GZIP` (a one-member single-file compressor)

---

### Requirement: Self-extracting (SFX) archives are detected behind an executable stub

RAR and 7z archives are sometimes distributed as self-extracting executables: an EXE
stub precedes the archive payload. When the leading bytes look like an executable
(the DOS/PE `MZ` header `4D 5A`, or ELF `7F 45 4C 46`) rather than a known archive
magic, detection SHALL scan for an embedded archive signature — the RAR
(`52 61 72 21 1A 07`) or 7z (`37 7A BC AF 27 1C`) magic — within a bounded forward
window, and/or near the end of the file where SFX payloads commonly sit. On a match
it SHALL report the embedded format with `payload_offset` set to the byte offset of
the payload; when no embedded signature is found it falls through (extension, else
`FormatDetectionError`). The native RAR and 7z parsers SHALL accept a start offset so
an SFX payload is read in place without copying. (This is a known gap in the DEV
detector, which carried partial SFX-handling code.)

#### Scenario: 7z payload behind a PE stub

- **WHEN** a file begins with `4D 5A` and the 7z magic `37 7A BC AF 27 1C` appears at offset `N`
- **THEN** `detect_format()` returns `ArchiveFormat.SEVEN_Z` with `payload_offset == N`, and the backend opens the archive starting at `N`

#### Scenario: executable with no embedded archive

- **WHEN** a file begins with an executable header but contains no RAR/7z signature in the scanned window
- **THEN** detection reports no SFX match and falls through to extension or `FormatDetectionError`

---

### Requirement: ISO 9660 requires an extended peek window

The system SHALL raise the peek window to 32 774 bytes when the source has a `.iso` extension or when ISO detection is being attempted, because the ISO 9660 primary volume descriptor begins at byte offset 32 769.

A stream shorter than 32 774 bytes simply cannot be an ISO. The system SHALL treat a
too-short stream as "not an ISO" and continue with the remaining detection steps
(other magic patterns, then extension) — it MUST NOT reject the source just because
it is too short for the ISO probe, since many other formats produce valid archives
far smaller than 32 KiB. Likewise, if the stream is long enough but the magic
`43 44 30 30 31` ("CD001") is not found at offset 32 769, the system SHALL record no
ISO match and fall through. `FormatDetectionError` is raised only when **no** format
matches at all, per the general detection algorithm.

#### Scenario: ISO file detected via extended peek

- **WHEN** the source is a path with extension `.iso` and the stream is at least 32 774 bytes
- **AND** the bytes `43 44 30 30 31` appear at offset 32 769
- **THEN** `detect_format()` returns `FormatInfo(format=ArchiveFormat.ISO, confidence=1.0, detected_by="magic")`

#### Scenario: stream too short for ISO is ruled out, not rejected

- **WHEN** a stream shorter than 32 774 bytes is examined
- **THEN** ISO is ruled out and detection falls through to the other magic patterns and extension
- **AND** `detect_format()` raises `FormatDetectionError` only if no other format matches — never solely because the stream was too short for the ISO probe

#### Scenario: a small archive of another format is still detected

- **WHEN** a 2 KiB file begins with the ZIP magic `50 4B 03 04`
- **THEN** `detect_format()` returns `ArchiveFormat.ZIP` even though the stream is far shorter than the ISO probe window

---

### Requirement: Detection never consumes or discards bytes

The bytes read during detection MUST remain available to the backend that
subsequently opens the archive. Wrapping a non-seekable source is the **opener's**
responsibility, not `detect_format()`'s, so that one wrapper is shared by detection
and the backend rather than detection consuming bytes the caller can no longer reach:

- For **paths and seekable streams**: detection reads via `peek`/`read` and restores
  the position with `seek(0)` afterwards; no wrapper is needed.
- For **non-seekable streams**: `open_archive()` SHALL wrap the source in a
  `PeekableStream` **before** running detection and pass that same `PeekableStream`
  to both detection and the backend. Detection inspects bytes through
  `PeekableStream.peek(n)` and consumes nothing.

Because `detect_format()` returns a `FormatInfo` only (it does not return a stream),
the standalone function is non-consuming for paths and seekable streams; for a raw
non-seekable stream the caller MUST pass a `PeekableStream` (or other
peekable/seekable wrapper) if it intends to keep reading the source afterwards — an
unwrapped non-seekable stream would lose the peeked prefix. `open_archive()` does
this wrapping internally, so callers of the high-level API never wrap by hand.

`PeekableStream` behaviour:

- Buffers the first `DETECTION_LIMIT` bytes (4 096 bytes by default; 32 774 bytes when ISO detection is triggered) in memory.
- Exposes a `.peek(n)` method that returns buffered bytes without consuming them.
- Presents itself as a `BinaryIO`-compatible object to the backend. Reads drain from the buffer first, then fall through to the underlying stream once the buffer is exhausted.
- Constructed by the opener for non-seekable sources. The backend receives the `PeekableStream` object and does not need to know whether the original source was seekable.

```
┌──────────────────────────────────────────────────────────────┐
│  PeekableStream                                              │
│  ┌─────────────────────┐    ┌────────────────────────────┐  │
│  │ buffer: bytearray   │    │ underlying: RawIO (socket) │  │
│  │ (first LIMIT bytes) │    │                            │  │
│  └─────────────────────┘    └────────────────────────────┘  │
│   └──► replayed on first read    └──► then transparently    │
│         by backend                     passed through        │
└──────────────────────────────────────────────────────────────┘
```

#### Scenario: seekable stream is rewound

- **WHEN** `detect_format()` is called with a seekable `BinaryIO` at position 0
- **THEN** after detection completes the stream position SHALL be 0
- **AND** the backend can read the full stream from the start without any data loss

#### Scenario: non-seekable source wrapped once by the opener and shared

- **WHEN** `open_archive()` is called with a non-seekable `BinaryIO` (e.g. a socket or pipe)
- **THEN** the opener wraps it in a `PeekableStream` before detection, runs detection via `peek()`, and hands the *same* `PeekableStream` to the backend
- **AND** the backend reads the peeked bytes first from the buffer, then continues from the underlying stream, with no bytes dropped

#### Scenario: standalone detect_format on a raw non-seekable stream

- **WHEN** `detect_format()` is called directly on a raw, unwrapped non-seekable stream that the caller intends to keep reading
- **THEN** the caller must pass a `PeekableStream` so the peeked prefix is replayed afterwards; a raw non-seekable stream would otherwise lose the bytes detection consumed
