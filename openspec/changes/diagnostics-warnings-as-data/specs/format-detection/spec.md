# format-detection — operation diagnostics

## MODIFIED Requirements

### Requirement: detect_format() returns a FormatInfo

The system SHALL expose:

```python
archivey.detect_format(
    source: str | Path | BinaryIO,
    *,
    config: ArchiveyConfig | None = None,
) -> FormatInfo
```

`config=None` selects the immutable library default. `FormatInfo` remains frozen and SHALL
add the final bounded summary for this detection operation:

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

`confidence` retains its discrete exact-magic / structural-probe / extension-guess
meaning. `encoding_hint` remains format-signal-only (never a member scan), and
`payload_offset > 0` remains the SFX indicator.

Standalone detection creates one finite collector and applies the configured policy,
callback, logging, and retention budget. When detection runs inside
`open_archive(config=...)`, open creates one prospective-reader collector and a detection
watermark first, then passes that collector into the internal detection routine. On
success, the reader assumes ownership of that exact collector. The implementation SHALL
NOT seed, merge, replay, or copy detection occurrences into another collector, and SHALL
charge each retained occurrence against the one budget exactly once.

The internal `FormatInfo.diagnostics` is the immutable point-in-time detection-range
snapshot needed by detection consumers. Since `open_archive()` returns only the reader,
the library SHALL not retain that internal snapshot after handoff; the same events remain
available through the reader's cumulative collector.

#### Scenario: standalone detection returns its final summary

- **WHEN** `detect_format(path)` completes with a magic/extension conflict
- **THEN** the returned `FormatInfo.diagnostics` contains the exact conflict count and its retained detail under the default budget

#### Scenario: open detection transfers rather than seeds

- **WHEN** automatic detection inside `open_archive()` emits and retains a conflict before backend construction succeeds
- **THEN** the reader continues the same collector, occurrence order, and budget, with no copied aggregate reference

#### Scenario: magic byte match

- **WHEN** the source's leading bytes match a known magic pattern
- **THEN** `detect_format()` returns `FormatInfo` with `confidence=CERTAIN` and `detected_by="magic"`

#### Scenario: extension-only fallback

- **WHEN** a path has no recognized content signature but has a known extension
- **THEN** `detect_format()` returns `FormatInfo` with `confidence=GUESS` and `detected_by="extension"`

#### Scenario: detect_format honors explicit policy

- **WHEN** `detect_format(path, config=ArchiveyConfig(diagnostic_policy=...))` encounters a conflict
- **THEN** the configured IGNORE/COLLECT/RAISE behavior applies to that finite detection operation

### Requirement: Conflict resolution — magic wins and warning is emitted

The system SHALL prefer the magic/content result over the extension result whenever the
existing detection precedence rules select it. A genuine mismatch SHALL emit
`FORMAT_EXTENSION_CONFLICT` with typed context containing the source display name and the
extension-suggested and content-detected format strings. The occurrence SHALL be counted
on and, under `COLLECT`/`RAISE` and available budget, retained by
`FormatInfo.diagnostics`. It SHALL be logged through `archivey.detection` according to
diagnostic policy.

The occurrence SHALL NOT be attached to `ArchiveInfo`. If detection creates a reader, it
SHALL already belong to the collector transferred to that reader.

#### Scenario: mismatched extension and magic

- **WHEN** a file named `archive.tar.gz` has 7-Zip magic
- **THEN** detection selects `ArchiveFormat.SEVEN_Z`, emits `FORMAT_EXTENSION_CONFLICT` on `FormatInfo`, and default policy logs it through `archivey.detection`

#### Scenario: conflict escalation prevents open

- **WHEN** `open_archive()` uses a policy that raises `FORMAT_EXTENSION_CONFLICT`
- **THEN** `DiagnosticRaisedError` is raised during detection and no reader is returned

#### Scenario: explicit format override has no detection event

- **WHEN** `open_archive(source, format=ArchiveFormat.ZIP)` bypasses automatic detection
- **THEN** no format-conflict diagnostic is emitted into the reader's collector
