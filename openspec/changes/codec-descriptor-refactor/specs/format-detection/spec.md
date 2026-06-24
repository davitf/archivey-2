# Format Detection — delta (codec-descriptor refactor)

## MODIFIED Requirements

### Requirement: Magic/extension/probe tables are aggregated from backends and codec descriptors

The detector SHALL build its magic, extension, and content-probe tables from two data
sources — the container format backends (`ReadBackend.MAGIC` / `EXTENSIONS`) and the
stream-codec descriptors — with no per-format `detect()` logic in either. The stream-codec
magic, weak-flag, extension, and content-probe rows that were previously declared on
`SingleFileBackend` SHALL be sourced from the descriptors instead, and detection results
(strong-vs-weak magic ordering, the zlib weak+probe path, the Brotli magic-less probe, and
the magic/extension conflict warning) MUST be unchanged.

#### Scenario: stream-codec magic comes from the descriptors

- **WHEN** `detect_format()` runs after the refactor on a `.gz` / `.zst` / zlib / Brotli source
- **THEN** the format, confidence, and `detected_by` are identical to before, with the magic/probe rows now drawn from the codec descriptors rather than hand-listed on `SingleFileBackend`

#### Scenario: container magic still comes from the format backends

- **WHEN** a ZIP / TAR / ISO source is detected
- **THEN** the match is driven by the container backend's `MAGIC` (unchanged), merged into the same aggregated table as the codec-descriptor rows
