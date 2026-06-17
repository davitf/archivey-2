# Testing Contract

## Purpose

The test suite must verify that all supported formats produce uniform, interchangeable `ArchiveMember` objects; that adversarial archives are rejected safely; that every writable format round-trips without data loss; and that every streaming backend operates correctly on non-seekable inputs.

## Requirements

### Requirement: Equivalence matrix across formats

The system SHALL produce identical `ArchiveMember` objects from ZIP, TAR, 7z, RAR, and ISO sources when reading a canonical directory structure (files, symlinks, nested directories, empty directories, filenames with unicode and spaces). Equivalence is defined as field-by-field equality excluding `sequence`, `raw_name`, `compressed_size`, `hashes`, and `extra`. Format-specific limitation flags (`ArchiveFormatFeatures`) encode per-format expected deviations and are used by the assertion helper to limit the comparison to the fields each format can faithfully represent.

#### Scenario: same canonical structure, multiple formats

- **WHEN** the same canonical directory structure is archived into ZIP, TAR, 7z, RAR, and ISO
- **THEN** the `ArchiveMember` objects produced by reading each archive are equal on all fields except `sequence`, `raw_name`, `compressed_size`, and `extra`
- **AND** any per-format field limitations are captured in `ArchiveFormatFeatures` flags rather than silently excluded from the comparison

### Requirement: Adversarial corpus coverage

The system SHALL include an adversarial test corpus that exercises every documented attack category and verifies that the correct exception is raised or limit is enforced in each case. The required adversarial cases are:

| Case | Expected outcome |
|---|---|
| Zip bomb — quine-style and nested (42.zip variant) | `max_ratio` and `max_extracted_bytes` limits enforced |
| Ratio-floor false positive — tiny highly-compressible file (10 B → 15 KiB, 1500:1) | Extracts **without** error; output stays under `ratio_activation_threshold` |
| Path traversal — `../evil`, `../../etc/passwd`, `./../../outside` | `PathTraversalError` raised |
| Absolute paths — `/etc/passwd`, `C:\Windows\System32\evil.dll` | `PathTraversalError` raised |
| Symlink escape — symlink pointing to `../../outside`, and chained symlinks | `SymlinkEscapeError` raised |
| Symlink loop — cyclic symlinks (`a → b`, `b → a`) | `SymlinkEscapeError` raised; no uncaught `OSError`/crash |
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

### Requirement: Cross-validate native readers against reference oracles

The system SHALL validate the native 7-Zip and RAR readers against reference
implementations used purely as test oracles: `py7zr` and the `7z` CLI for 7-Zip,
and `rarfile` and the `unrar` CLI for RAR. For a representative corpus of
archives, the native reader's member metadata and decompressed bytes MUST match
the oracle's. These oracle libraries are `dev`-group dependencies only and are
never required at runtime; oracle-backed tests SHALL be skipped (not failed) when
the oracle library or CLI tool is unavailable in the environment.

The corpus MUST exercise the core codecs the native 7z reader supports without
extras (LZMA1, LZMA2, simple BCJ filters, Delta, BZip2, Deflate, STORED) and —
when the relevant extras are installed — PPMd / Deflate64 (`[7z]`) and
AES-encrypted archives (`[crypto]`). It MUST assert that genuinely unsupported
codecs (BCJ2, and unrecognized method IDs) raise the documented "unsupported
codec" error rather than diverging silently from the oracle.

#### Scenario: native 7z reader matches the py7zr oracle

- **WHEN** a 7-Zip archive in the corpus is read by both the native reader and `py7zr`
- **THEN** member metadata and decompressed bytes are identical between the two
- **AND** the test is skipped (not failed) if `py7zr` is not installed

#### Scenario: native RAR reader matches the rarfile/unrar oracle

- **WHEN** a RAR archive in the corpus is read by both the native reader and `rarfile`/`unrar`
- **THEN** member metadata and decompressed bytes are identical between the two
- **AND** the test is skipped if `rarfile` or the `unrar` binary is unavailable

#### Scenario: unsupported 7z codec is rejected, not guessed

- **WHEN** a 7-Zip archive using BCJ2 (or an unrecognized method ID) is read by the native reader
- **THEN** the documented unsupported-codec error is raised, rather than returning bytes that disagree with the oracle

### Requirement: Non-seekable stream coverage for every streaming backend

The system SHALL test every backend that supports streaming with a `FakeNonSeekable` wrapper that raises `io.UnsupportedOperation` on all `seek` and `tell` calls. The test verifies that the backend reads and iterates correctly when the source stream cannot be repositioned.

#### Scenario: non-seekable ZIP source

- **WHEN** a ZIP archive is opened through a `FakeNonSeekable` wrapper
- **THEN** the backend reads all members correctly without calling `seek` or `tell` on the underlying stream

#### Scenario: non-seekable TAR.GZ source

- **WHEN** a `.tar.gz` archive is opened through a `FakeNonSeekable` wrapper
- **THEN** all members are iterable and their data is readable without error
