# Progress Reporting and Logging

## Purpose

Progress reporting and logging give callers visibility into extraction as it happens and into internal library events. Progress is delivered via a callback receiving typed dataclass snapshots; logging uses the standard Python `logging` module under a named hierarchy that the library never configures itself.

## Requirements

### Requirement: Progress Reporting via on_progress Callback

The system SHALL accept an optional `on_progress` callback on `archivey.extract()` and `ArchiveReader.extract_all()`. The callback, if provided, SHALL be called once per member as that member is processed, receiving an `ExtractionProgress` instance.

```python
@dataclass
class ExtractionProgress:
    member: Member
    bytes_written: int
    total_bytes_estimated: int | None   # None if archive has no size info
    members_done: int
    members_total: int | None
```

`total_bytes_estimated` is `None` when the archive format does not provide uncompressed size information. `members_total` is `None` when the total member count cannot be known without a full scan.

#### Scenario: callback invoked per member

- **WHEN** `archivey.extract("archive.zip", "/dest/", on_progress=cb)` is called
- **THEN** `cb` is invoked once for each member processed, with an `ExtractionProgress` carrying that member, cumulative `bytes_written`, and counters for members completed and total

#### Scenario: total_bytes_estimated is None for formats without size info

- **WHEN** the archive format cannot provide uncompressed sizes (e.g. a GZ stream)
- **THEN** `ExtractionProgress.total_bytes_estimated` is `None` for every callback invocation

---

### Requirement: Per-Member ExtractionResult with Status

The system SHALL return a `list[ExtractionResult]` from `archivey.extract()` and `ArchiveReader.extract_all()`, with one entry per member processed. Each result SHALL carry the member, the path it was written to (or `None` if not written), and an `ExtractionStatus`.

```python
@dataclass
class ExtractionResult:
    member: Member
    path: Path | None           # None if skipped
    status: ExtractionStatus    # EXTRACTED, SKIPPED, REJECTED

class ExtractionStatus(Enum):
    EXTRACTED = "extracted"
    SKIPPED   = "skipped"       # due to OverwritePolicy.SKIP
    REJECTED  = "rejected"      # due to filter rejection; no exception raised if
                                # on_rejection=OnRejection.WARN (default: RAISE)
```

#### Scenario: successfully extracted member

- **WHEN** a member is written to disk without error
- **THEN** its `ExtractionResult` has `status=ExtractionStatus.EXTRACTED` and `path` pointing to the file on disk

#### Scenario: skipped member due to OverwritePolicy.SKIP

- **WHEN** a member's destination path already exists and `OverwritePolicy.SKIP` is active
- **THEN** the member's `ExtractionResult` has `status=ExtractionStatus.SKIPPED` and `path=None`

#### Scenario: rejected member due to filter

- **WHEN** a member is blocked by a safety filter and the rejection policy is WARN rather than RAISE
- **THEN** the member's `ExtractionResult` has `status=ExtractionStatus.REJECTED` and `path=None`, and no exception is raised

---

### Requirement: Logging Under the archivey Logger Hierarchy

The system SHALL emit all log messages via `logging.getLogger("archivey")` and its named children. The library SHALL NOT configure any handlers, levels, or formatters — that is left entirely to the application.

The named child loggers are:

| Logger | Events |
|---|---|
| `archivey.detection` | Format detection events |
| `archivey.normalization` | Path normalization changes (warnings when `name` differs from `original_name`) |
| `archivey.extraction` | Extraction events and filter decisions |
| `archivey.backends.*` | Backend-specific debug messages |

#### Scenario: library emits no output by default

- **WHEN** the application has not configured any handlers on the `archivey` logger or its ancestors
- **THEN** no output is produced, in accordance with Python's default "no handler" behaviour; the library never installs a handler itself

#### Scenario: format detection conflict logged as WARNING

- **WHEN** format detection finds a magic-byte match that conflicts with the file extension
- **THEN** a `logging.WARNING` is emitted on `archivey.detection`

#### Scenario: path normalization change logged

- **WHEN** normalizing a member's `name` changes its logical meaning compared to `original_name`
- **THEN** a warning is emitted via `archivey.normalization`
