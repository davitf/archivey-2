# Command-Line Interface

## Purpose

Archivey exposes an `archivey` command-line tool (a DEV feature) that allows users to inspect, verify, and extract archives directly from the shell. It supports `list`, `test`, and `extract` subcommands along with fnmatch filename patterns and optional I/O instrumentation.

## Requirements

### Requirement: `archivey` command with list, test, and extract subcommands

The system SHALL provide an `archivey` command that exposes at minimum the following subcommands: `list`, `test`, and `extract`. The CLI supports fnmatch filename patterns for filtering members and a `--track-io` flag for I/O instrumentation. The CLI depends on `tqdm` for progress output, which is provided via the `[cli]` optional extra; the core library itself has no hard dependency on `tqdm`.

#### Scenario: listing archive contents

- **WHEN** the user runs `archivey list <archive>`
- **THEN** the command prints the members of the archive to stdout

#### Scenario: verifying archive integrity

- **WHEN** the user runs `archivey test <archive>`
- **THEN** the command reads every member fully, verifying each stored digest (e.g. CRC32; Blake2sp for RAR5) via the shared verification stage, and reports any failures

#### Scenario: extracting archive contents

- **WHEN** the user runs `archivey extract <archive> [dest]`
- **THEN** the command extracts the archive to the destination directory using the library's default safe-extraction policy

#### Scenario: filtering by filename pattern

- **WHEN** the user supplies an fnmatch pattern to any subcommand (e.g. `archivey list archive.zip "*.py"`)
- **THEN** the command limits its output or operation to members whose names match the pattern

#### Scenario: CLI installed without the `[cli]` extra

- **WHEN** `tqdm` is not installed (the `[cli]` extra is absent)
- **THEN** progress output is suppressed or the command emits a clear error indicating the missing extra, and the core library remains importable and functional
