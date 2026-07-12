# Format Detection

## Purpose

Identify archive format of a path or binary stream without fully opening it.
Returns frozen `FormatInfo` (format, confidence, encoding hint, optional SFX
offset, detection diagnostics). Detection never discards bytes the opener still
needs.

## Related specs

| Spec | Relationship |
| --- | --- |
| `archive-reading` | Auto-detect inside `open_archive`; caller sees events on `reader.diagnostics` |
| `diagnostics` | Collector/budget/policy; this spec owns the open handoff |
| `backend-registry` | Container `MAGIC` / `EXTENSIONS` / `CONTENT_PROBES` |
| `compressed-streams` | Codec descriptors supply stream-codec magic/probes |

## Requirements

### Requirement: detect_format() returns a FormatInfo

The system SHALL expose:

```python
archivey.detect_format(
    source: str | Path | BinaryIO,
    *,
    config: ArchiveyConfig | None = None,
) -> FormatInfo
```

```python
class DetectionConfidence(Enum):
    CERTAIN = "certain"
    PROBABLE = "probable"
    GUESS = "guess"

@dataclass(frozen=True)
class FormatInfo:
    format: ArchiveFormat
    confidence: DetectionConfidence
    detected_by: str
    encoding_hint: str | None
    payload_offset: int = 0
    diagnostics: DiagnosticSummary = DiagnosticSummary.empty()
```

`config=None` → library default. `confidence` = magic / structural probe /
extension-guess. `encoding_hint` is format-signal only (never a member scan).
`payload_offset > 0` marks an SFX payload start.

**Collectors:**

| Path | Behavior |
| --- | --- |
| Standalone `detect_format` | One finite collector; policy/callback/logging/budget; final summary on `FormatInfo.diagnostics` |
| Inside `open_archive` | Open creates prospective-reader collector + detection watermark, passes that collector into detection. On success the reader owns it — no seed/merge/replay/copy; each retained occurrence charged once. Internal detection-range `FormatInfo.diagnostics` is not retained after handoff; same events remain on the reader's cumulative summary |

#### Scenario: detect / handoff matrix

| Case | Expected |
| --- | --- |
| Standalone detect with magic/extension conflict | `FormatInfo.diagnostics` has exact conflict count + retained detail under default budget |
| Auto-detect inside `open_archive` retains conflict, open succeeds | Reader continues same collector/order/budget; no copied aggregate |
| Magic match | `confidence=CERTAIN`, `detected_by="magic"` |
| Extension-only guess | `confidence=GUESS`, `detected_by="extension"` |
| Explicit `diagnostic_policy` on detect | IGNORE/COLLECT/RAISE applies to that finite detection |

### Requirement: Magic-first detection with extension fallback and confidence scoring

The system SHALL execute format detection with this algorithm:

1. Read up to `DETECTION_LIMIT` bytes (default 4096) from the source.
2. Match against the magic-byte table (exact offsets).
3. Match → `CERTAIN` / `detected_by="magic"`.
4. Else, if `Path` with known extension → `GUESS` / `detected_by="extension"`.
5. Else → content probes, then fail (`FormatDetectionError` when nothing matches).

#### Scenario: unrecognised bytes, no path

| Case | Expected |
| --- | --- |
| Non-seekable `BinaryIO`, no filename, no magic | `FormatDetectionError` |

### Requirement: Conflict resolution — magic wins and warning is emitted

Magic/content result wins per existing precedence. A genuine mismatch SHALL emit
`FORMAT_EXTENSION_CONFLICT` with typed context (source display name, extension
format, content format). Counted on `FormatInfo.diagnostics`; under
`COLLECT`/`RAISE` + budget, retained; logged via `archivey.detection` per policy.
SHALL NOT attach to `ArchiveInfo`. If a reader is created, the occurrence already
belongs to the transferred collector.

#### Scenario: conflict matrix

| Case | Expected |
| --- | --- |
| `archive.tar.gz` with 7z magic | `SEVEN_Z` + `FORMAT_EXTENSION_CONFLICT` on `FormatInfo`; default policy logs on `archivey.detection` |
| `open_archive` policy raises on conflict | `DiagnosticRaisedError` during detection; no reader |
| `open_archive(..., format=ZIP)` | No format-conflict diagnostic |

### Requirement: Magic/extension/probe tables are aggregated from backends and codec descriptors

Detector tables SHALL come from container backends (`ReadBackend.MAGIC` /
`EXTENSIONS` / `CONTENT_PROBES`) and stream-codec descriptors — no per-format
`detect()` logic. Stream-codec rows come from descriptors (not hand-listed on
`SingleFileBackend`). A content probe is the codec's `content_probe` function.
Detected formats/confidence/`detected_by` MUST match prior behavior.

#### Scenario: table sources matrix

| Case | Expected |
| --- | --- |
| `.gz` / `.zst` | Same result as before; magic from codec descriptors |
| zlib / Brotli | `PROBABLE` / `content_probe` from descriptor functions |
| ZIP / TAR / ISO | Container backend `MAGIC`, merged into the same table |

### Requirement: Magic-byte table

Exact matches only (no fuzzy/weak magic). Recognised:

| Format | Signature (summary) |
| --- | --- |
| ZIP | `50 4B 03 04` / `07 08` / `05 06` |
| GZip | `1F 8B` |
| BZip2 | `42 5A 68` |
| XZ | `FD 37 7A 58 5A 00` |
| Zstandard | `28 B5 2F FD` |
| 7-Zip | `37 7A BC AF 27 1C` |
| RAR 4.x / 5.x | `52 61 72 21 1A 07 00` / `… 01 00` |
| ISO 9660 | `CD001` at 32769 |
| TAR | `ustar` at 257 |
| LZ4 | `04 22 4D 18` |
| lzip | `LZIP` |
| unix-compress | `1F 9D` |

Formats without reliable exact magic (notably **zlib**) SHALL NOT appear here —
content probe only.

#### Scenario: magic matrix

| Case | Expected |
| --- | --- |
| Starts `50 4B 03 04` | ZIP, `CERTAIN`, `magic` |
| Magic table consulted for zlib | No zlib entry; CMF/FLG → zlib probe |
| `ustar` at 257, ≥512 bytes | TAR, `CERTAIN`, `magic` |

### Requirement: Magic-less formats are detected by a content probe

When the magic-byte table yields no match, the system SHALL run each registered
content probe on the peeked prefix (consumes nothing). This covers Brotli (no
signature) and zlib (too-unspecific CMF/FLG). Match → `PROBABLE` /
`detected_by="content_probe"`. Probes typically decode a bounded prefix; MAY gate
on cheap structural bytes first. Skip when the decompressor backend is missing
(fall through to extension). Extension MAY override a disagreeing probe
(false-positive risk on short/adversarial input).

#### Scenario: content-probe matrix

| Case | Expected |
| --- | --- |
| No magic; bounded prefix decompresses as Brotli | `BROTLI`, `PROBABLE`, `content_probe` |
| zlib CMF/FLG + clean zlib decode | `ZLIB`, `PROBABLE`, `content_probe` |
| zlib-looking header, decode fails | No zlib claim; fall through to extension / fail |
| `.br`, Brotli extra missing | Probe skipped; extension guess `BROTLI`/`GUESS` |

### Requirement: Compressed streams are probed for an inner TAR

For single-file compressors (gzip, bzip2, xz, zstd, lz4, lzip, zlib, brotli,
unix-compress), detection SHALL decompress a bounded amount of *content* and look
for TAR `ustar` at offset 257, reporting combined formats (`TAR_GZ`, …) when
present. Need ≥512 decompressed bytes.

Compressed input is supplied via a **bounded, non-consuming view** (up to
`_INNER_TAR_MAX_PROBE_BYTES`, ≥ largest bzip2 first-block compressed size):

- Stream codecs pull incrementally (first few KiB usually enough).
- Block-transform (bzip2) may pull a full first block before any output.

Seekable: read + restore position. Path: open/close. Non-seekable: buffer in
`PeekableStream` for replay. Use sequential decompression (not random-access
accelerators that reject bounded non-seekable views). Missing decompressor → bare
compressor format; open may refine. No TAR header within the bound → bare
compressor.

#### Scenario: inner-TAR matrix

| Case | Expected |
| --- | --- |
| `.gz` → content with `ustar`@257 | `TAR_GZ` (not bare `GZIP`) |
| `.gz` → non-TAR content | `GZIP` |
| `.tar.bz2` with large first block (> peek prefix) | Read up to max block; `TAR_BZ2` |
| Large-block bare `.bz2`, no `ustar` | Bounded read; `BZ2` (no false promotion) |
| Non-seekable `.tar.bz2` needing full block | Buffered in `PeekableStream`; `TAR_BZ2`; backend can still read all |

### Requirement: Self-extracting (SFX) archives are detected behind an executable stub

SFX RAR/7z: EXE stub precedes payload. If leading bytes look like executable
(`MZ` / ELF) rather than archive magic, scan for RAR (`52 61 72 21 1A 07`) or 7z
(`37 7A BC AF 27 1C`) magic within a bounded forward window and/or near EOF. Match
→ embedded format with `payload_offset` = payload start. No match → fall through
(extension / `FormatDetectionError`). Native RAR/7z parsers SHALL accept a start
offset (read in place, no copy).

#### Scenario: SFX matrix

| Case | Expected |
| --- | --- |
| `MZ` + 7z magic at offset N | `SEVEN_Z`, `payload_offset == N`; backend opens at N |
| Executable header, no RAR/7z in window | No SFX match; extension or `FormatDetectionError` |

### Requirement: ISO 9660 requires an extended peek window

The system SHALL raise the peek window to 32774 bytes when `.iso` or ISO
detection is attempted (PVD at 32769). A stream shorter than that SHALL rule out
ISO and continue (other magic / extension) — never reject solely for being too
short for ISO. Long enough but no `CD001`@32769 → no ISO match, fall through.
`FormatDetectionError` only when **no** format matches.

#### Scenario: ISO peek matrix

| Case | Expected |
| --- | --- |
| `.iso`, ≥32774 bytes, `CD001`@32769 | ISO, `CERTAIN`, `magic` |
| Stream < 32774 bytes | ISO ruled out; fall through; error only if nothing else matches |
| 2 KiB file with ZIP magic | ZIP (despite short of ISO window) |

### Requirement: Detection never consumes or discards bytes

Bytes inspected during detection MUST remain available to the backend. Wrapping
non-seekable sources is the **opener's** job so one wrapper is shared:

| Source | Behavior |
| --- | --- |
| Path / seekable stream | Peek/read then restore entry `tell()`. Archive begins where the caller positioned. `open_archive` may wrap a mid-file seekable stream in a zero-origin view (`SlicingStream`) so absolute-offset backends (e.g. ISO/`pycdlib`) see origin 0. |
| Non-seekable | `open_archive` wraps in `PeekableStream` **before** detection and passes the **same** wrapper to detection and backend. Detection uses `peek(n)` only. |

Standalone `detect_format` is non-consuming for paths/seekable streams. For a raw
non-seekable stream the caller must pass a `PeekableStream` (or equivalent) if it
will keep reading — otherwise the peeked prefix is lost. `open_archive` wraps
internally.

`PeekableStream`: buffers first `DETECTION_LIMIT` bytes (32774 when ISO triggered);
`.peek(n)` without consume; `BinaryIO` to backend (drain buffer, then underlying).

#### Scenario: non-consuming matrix

| Case | Expected |
| --- | --- |
| Seekable `BinaryIO` at position N | After detect, position is N again; backend can read full archive |
| `open_archive` on non-seekable | One `PeekableStream` for detect + backend; peeked bytes replay then fall through |
| Standalone detect on raw non-seekable the caller will reread | Caller must supply `PeekableStream` |
