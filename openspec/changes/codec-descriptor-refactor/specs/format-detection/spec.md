# Format Detection — delta (codec-descriptor refactor)

## MODIFIED Requirements

### Requirement: Magic/extension/probe tables are aggregated from backends and codec descriptors

The detector SHALL build its magic, extension, and content-probe tables from two data
sources — the container format backends (`ReadBackend.MAGIC` / `EXTENSIONS` /
`CONTENT_PROBES`) and the stream-codec descriptors — with no per-format `detect()` logic in
either. The stream-codec magic, extension, and content-probe rows that were previously
declared on `SingleFileBackend` SHALL be sourced from the descriptors instead. A
content probe SHALL be a per-format function (the codec descriptor's `content_probe`)
rather than a generic detector routine keyed by format, so each codec owns its own
recognition logic. Detection results (the formats detected, their confidence, and
`detected_by`) MUST be unchanged.

#### Scenario: stream-codec magic comes from the descriptors

- **WHEN** `detect_format()` runs after the refactor on a `.gz` / `.zst` source
- **THEN** the format, confidence, and `detected_by` are identical to before, with the magic rows now drawn from the codec descriptors rather than hand-listed on `SingleFileBackend`

#### Scenario: content probes come from the descriptors as functions

- **WHEN** `detect_format()` runs on a zlib or Brotli source after the refactor
- **THEN** the result is identical to before (`ArchiveFormat.ZLIB` / `ArchiveFormat.BROTLI`, `PROBABLE`, `content_probe`), with the probe supplied by the codec descriptor's `content_probe` function rather than a generic detector routine

#### Scenario: container magic still comes from the format backends

- **WHEN** a ZIP / TAR / ISO source is detected
- **THEN** the match is driven by the container backend's `MAGIC` (unchanged), merged into the same aggregated table as the codec-descriptor rows

### Requirement: Magic-byte table

The system SHALL recognise formats by inspecting bytes at specified offsets. All matches
are exact; no fuzzy, heuristic, or "weak" matching is performed. The recognised exact-magic
formats are: ZIP (`50 4B 03 04` / `50 4B 07 08` / `50 4B 05 06`), GZip (`1F 8B`), BZip2
(`42 5A 68`), XZ (`FD 37 7A 58 5A 00`), Zstandard (`28 B5 2F FD`), 7-Zip
(`37 7A BC AF 27 1C`), RAR 4.x (`52 61 72 21 1A 07 00`), RAR 5.x
(`52 61 72 21 1A 07 01 00`), ISO 9660 (`CD001` at 32 769), TAR (`ustar` at 257), LZ4
(`04 22 4D 18`), lzip (`LZIP`), and unix-compress (`1F 9D`).

Formats whose leading bytes are **not** a reliable exact magic SHALL NOT appear in this
table; they are recognised by a content probe instead (see the content-probe requirement).
In particular, **zlib** (whose 2-byte CMF/FLG header is not a true magic — the same prefix
begins many raw-deflate streams and can occur in arbitrary data) is detected by a content
probe, not by a magic-table entry.

#### Scenario: ZIP standard local file header

- **WHEN** the source begins with bytes `50 4B 03 04`
- **THEN** `detect_format()` returns `FormatInfo(format=ArchiveFormat.ZIP, confidence=DetectionConfidence.CERTAIN, detected_by="magic")`

#### Scenario: zlib is not an exact-magic entry

- **WHEN** the magic-byte table is consulted
- **THEN** it contains no zlib entry; a `78 9C` (or other CMF/FLG) prefix is resolved by the zlib content probe, not by an exact or "weak" magic match

### Requirement: Magic-less formats are detected by a content probe

When the magic-byte table yields no match, the system SHALL run each registered content
probe — a per-format function that inspects the peeked prefix and returns whether it
matches. This covers single-file compressors not identified by an exact magic: Brotli
(no signature at all) and zlib (a 2-byte header too unspecific to trust on its own). A
probe match SHALL report `confidence=DetectionConfidence.PROBABLE` and
`detected_by="content_probe"` (a structural test, weaker than an exact magic match).
Content probes run only after all magic-byte matching has failed, and each probe MUST
operate on the already-peeked bytes so it consumes nothing from the source.

A probe typically decodes a bounded prefix through the codec and treats "decompresses
without error" as a match; a probe MAY first gate on cheap structural bytes (zlib's probe
checks its CMF/FLG header before attempting the decode, failing fast on non-zlib data). A
probe is **skipped** (returns no match) when its decompression backend is unavailable
(e.g. Brotli when the `[7z]` extra is not installed); detection then falls through to the
extension guess rather than failing. Because a content probe can have false positives on
short or adversarial inputs, an extension that disagrees with a probe result MAY override
it.

#### Scenario: standalone Brotli detected by content probe

- **WHEN** a source matches no magic pattern and a bounded prefix decompresses cleanly through the Brotli decompressor
- **THEN** `detect_format()` returns `FormatInfo(format=ArchiveFormat.BROTLI, confidence=DetectionConfidence.PROBABLE, detected_by="content_probe")`

#### Scenario: zlib detected by its content probe

- **WHEN** a source begins with a zlib CMF/FLG header and a bounded prefix decompresses cleanly through the zlib decompressor
- **THEN** `detect_format()` returns `FormatInfo(format=ArchiveFormat.ZLIB, confidence=DetectionConfidence.PROBABLE, detected_by="content_probe")`

#### Scenario: zlib header on non-zlib data falls through

- **WHEN** a source begins with a zlib CMF/FLG header but the prefix does not decode as zlib
- **THEN** the zlib probe reports no match and detection falls through to the extension guess (or fails if no extension is usable), never claiming zlib on the header alone

#### Scenario: probe skipped when the backend is missing

- **WHEN** a `.br` path matches no magic pattern and the Brotli backend is not installed
- **THEN** the content probe is skipped and detection falls back to the extension guess (`ArchiveFormat.BROTLI`, `confidence=DetectionConfidence.GUESS`, `detected_by="extension"`)
