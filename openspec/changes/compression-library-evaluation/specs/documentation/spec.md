# Documentation — delta (compression-library evaluation)

## ADDED Requirements

### Requirement: Per-format compression-library choices are documented

The documentation SHALL include a per-format compression-library analysis
(`docs/library-analysis.md`) that, for each codec the library reads, names the chosen
library, the alternatives considered, and the criteria behind the decision (non-seekable
support, efficient seeking, corruption detection, truncation detection, error-reporting
fidelity, install/availability, maintenance). Where a decision was already made and recorded
elsewhere, the analysis SHALL cite it — e.g. the native XZ parser choice in
`davitf/archivey-dev#214`.

#### Scenario: every read codec has a recorded rationale

- **WHEN** a contributor reads `docs/library-analysis.md`
- **THEN** for each codec (gzip, bzip2, xz/lzma, lzip, zstd, lz4, brotli, unix-compress, deflate64, ppmd) they find the chosen library, the rejected alternatives, and the reason

#### Scenario: an already-recorded decision is cited, not re-derived

- **WHEN** the analysis covers XZ
- **THEN** it states the native-parser decision and links `davitf/archivey-dev#214` rather than re-litigating it
