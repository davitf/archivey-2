# Testing Contract

## Purpose

The test suite must verify that all supported formats produce uniform, interchangeable `Member` objects; that adversarial archives are rejected safely; that every writable format round-trips without data loss; and that every streaming backend operates correctly on non-seekable inputs.

## Requirements

### Requirement: Equivalence matrix across formats

The system SHALL produce identical `Member` objects from ZIP, TAR, 7z, RAR, and ISO sources when reading a canonical directory structure (files, symlinks, nested directories, empty directories, filenames with unicode and spaces). Equivalence is defined as field-by-field equality excluding `sequence`, `original_name`, `compressed_size`, and `extra`. Format-specific limitation flags (`ArchiveFormatFeatures`) encode per-format expected deviations and are used by the assertion helper to limit the comparison to the fields each format can faithfully represent.

#### Scenario: same canonical structure, multiple formats

- **WHEN** the same canonical directory structure is archived into ZIP, TAR, 7z, RAR, and ISO
- **THEN** the `Member` objects produced by reading each archive are equal on all fields except `sequence`, `original_name`, `compressed_size`, and `extra`
- **AND** any per-format field limitations are captured in `ArchiveFormatFeatures` flags rather than silently excluded from the comparison

### Requirement: Adversarial corpus coverage

The system SHALL include an adversarial test corpus that exercises every documented attack category and verifies that the correct exception is raised or limit is enforced in each case. The required adversarial cases are:

| Case | Expected outcome |
|---|---|
| Zip bomb — quine-style and nested (42.zip variant) | `max_ratio` and `max_extracted_bytes` limits enforced |
| Path traversal — `../evil`, `../../etc/passwd`, `./../../outside` | `PathTraversalError` raised |
| Absolute paths — `/etc/passwd`, `C:\Windows\System32\evil.dll` | `PathTraversalError` raised |
| Symlink escape — symlink pointing to `../../outside`, and chained symlinks | `SymlinkEscapeError` raised |
| Corrupt archive — truncated ZIP (missing EOCD), truncated TAR, bad CRC | `CorruptionError` or `TruncatedError` raised |
| Unicode bombs — `\x00` in paths, RTL override characters in filenames | `PathTraversalError` raised (for null bytes); warning or rejection for RTL |
| Giant claimed size — member claims 1 TiB uncompressed but archive is 1 KiB | Extraction aborts cleanly before exhausting resources |

Adversarial archives are committed as binary fixtures under `tests/fixtures/adversarial/`. Regenerable fixtures are produced by `tests/create_adversarial.py`.

#### Scenario: zip bomb extraction

- **WHEN** a zip bomb archive is extracted with default limits
- **THEN** extraction raises `ExtractionError` before the `max_extracted_bytes` or `max_ratio` threshold is exceeded

#### Scenario: path traversal member

- **WHEN** an archive containing a member named `../evil` is extracted
- **THEN** extraction raises `PathTraversalError` and no file is written outside the destination

#### Scenario: corrupt archive

- **WHEN** an archive with a truncated or CRC-invalid member is read
- **THEN** `CorruptionError` or `TruncatedError` is raised with the original exception attached as `__cause__`

### Requirement: Round-trip test for every writable format

The system SHALL include a round-trip test for every writable format. The test sequence is `create → extract → compare` and must produce identical files and metadata within the format's documented timestamp and permission limitations.

#### Scenario: ZIP round-trip

- **WHEN** a canonical file set is written to a ZIP archive and then extracted
- **THEN** the extracted files match the originals in content and in all metadata fields the ZIP format can faithfully represent

#### Scenario: TAR round-trip

- **WHEN** a canonical file set is written to a TAR archive and then extracted
- **THEN** the extracted files match the originals in content and in all metadata fields the TAR format can faithfully represent

### Requirement: Non-seekable stream coverage for every streaming backend

The system SHALL test every backend that supports streaming with a `FakeNonSeekable` wrapper that raises `io.UnsupportedOperation` on all `seek` and `tell` calls. The test verifies that the backend reads and iterates correctly when the source stream cannot be repositioned.

#### Scenario: non-seekable ZIP source

- **WHEN** a ZIP archive is opened through a `FakeNonSeekable` wrapper
- **THEN** the backend reads all members correctly without calling `seek` or `tell` on the underlying stream

#### Scenario: non-seekable TAR.GZ source

- **WHEN** a `.tar.gz` archive is opened through a `FakeNonSeekable` wrapper
- **THEN** all members are iterable and their data is readable without error
