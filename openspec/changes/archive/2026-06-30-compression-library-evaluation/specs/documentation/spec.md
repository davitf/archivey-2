# Documentation — delta (compression-library evaluation)

## ADDED Requirements

### Requirement: Per-format compression-library choices are documented

The documentation SHALL include a per-format compression-library analysis
(`docs/library-analysis.md`) that, for each codec the library reads, names the chosen
library, the alternatives considered, and the criteria behind the decision (non-seekable
support, efficient seeking, corruption detection, truncation detection, error-reporting
fidelity, install/availability, maintenance). Each decision SHALL be documented **in full**
within the analysis — its rationale and the alternatives weighed — so the record does not
depend on external sources that may later be retired. An external origin (e.g.
`davitf/archivey-dev#214` for the native XZ parser) MAY be linked for provenance, but the link
SHALL NOT be a substitute for the recorded rationale.

#### Scenario: every read codec has a recorded rationale

- **WHEN** a contributor reads `docs/library-analysis.md`
- **THEN** for each codec (gzip, bzip2, xz/lzma, lzip, zstd, lz4, brotli, unix-compress, deflate64, ppmd) they find the chosen library, the rejected alternatives, and the reason

#### Scenario: a decision is documented in full, not merely cited

- **WHEN** the analysis covers XZ (a decision originally made in another repository)
- **THEN** it records the native-parser rationale in full — the alternatives considered (stdlib `lzma.open`, `python-xz`) and why each was rejected — self-containedly, linking `davitf/archivey-dev#214` only as provenance, so the decision survives the old repository being retired
