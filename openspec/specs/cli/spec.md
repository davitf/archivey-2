# Command-Line Interface

## Purpose

Shell interface for inspecting, verifying, and extracting archives with fnmatch filters and optional I/O instrumentation; CLI-only deps stay out of core.

## Related specs

| Spec | Relationship |
| --- | --- |
| `archive-reading` | Listing, member filtering, digest verification reads |
| `safe-extraction` | Default safe extraction policy used by `extract` |
| `packaging-and-extras` | `[cli]` extra supplies `tqdm`; core remains importable without it |
| `access-mode-and-cost` | Optional I/O instrumentation/cost reporting |

## Requirements

### Requirement: archivey command with list, test, and extract subcommands

The system SHALL provide an `archivey` command with `list`, `test`, and `extract`. It SHALL support fnmatch member filters and `--track-io`. Progress output SHALL use `tqdm` from `[cli]`; core MUST NOT depend on `tqdm`.

`list` SHALL print members to stdout. `test` SHALL fully read selected members and
verify stored digests through the shared verification stage, including CRC32 and
Blake2sp where supported. `extract` SHALL use the default safe-extraction policy.

#### Scenario: CLI behavior matrix

| Case | Expected |
| --- | --- |
| `archivey list <archive>` | Prints archive members to stdout |
| `archivey test <archive>` | Fully reads members, verifies stored digests, reports failures |
| `archivey extract <archive> [dest]` | Extracts with default safe-extraction policy |
| Subcommand includes fnmatch pattern, e.g. `archivey list archive.zip "*.py"` | Operation is limited to matching member names |
| `[cli]` extra absent / `tqdm` missing | Progress output is suppressed or a clear missing-extra error is emitted; core import and library API remain functional |
| `--track-io` supplied | Command reports the configured I/O instrumentation for the operation |
