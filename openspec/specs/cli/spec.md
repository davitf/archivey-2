# Command-Line Interface

## Purpose

Shell interface for inspecting, verifying, and extracting archives. The `archivey`
command exposes `list`, `test`, and `extract` subcommands, fnmatch member filters,
and optional I/O instrumentation while keeping CLI-only dependencies out of core.

## Related specs

| Spec | Relationship |
| --- | --- |
| `archive-reading` | Listing, member filtering, digest verification reads |
| `safe-extraction` | Default safe extraction policy used by `extract` |
| `diagnostics` | Advisory data that may be surfaced by CLI output |
| `logging` | CLI may rely on standard logging configuration |
| `packaging-and-extras` | `[cli]` extra supplies `tqdm`; core remains importable without it |
| `access-mode-and-cost` | Optional I/O instrumentation/cost reporting |

## Requirements

### Requirement: archivey command with list, test, and extract subcommands

The system SHALL provide an `archivey` command with at minimum `list`, `test`, and
`extract` subcommands. The command SHALL support fnmatch filename patterns for
filtering members and a `--track-io` flag for I/O instrumentation. Progress output
SHALL depend on `tqdm` from the `[cli]` extra; the core library MUST NOT have a hard
dependency on `tqdm`.

`list` SHALL print archive members to stdout. `test` SHALL read every selected
member fully and verify stored digests through the shared verification stage,
including CRC32 and Blake2sp where supported. `extract` SHALL extract selected
members to the destination directory using the library's default safe-extraction
policy.

#### Scenario: CLI behavior matrix

| Case | Expected |
| --- | --- |
| `archivey list <archive>` | Prints archive members to stdout |
| `archivey test <archive>` | Fully reads members, verifies stored digests, reports failures |
| `archivey extract <archive> [dest]` | Extracts with default safe-extraction policy |
| Subcommand includes fnmatch pattern, e.g. `archivey list archive.zip "*.py"` | Operation is limited to matching member names |
| `[cli]` extra absent / `tqdm` missing | Progress output is suppressed or a clear missing-extra error is emitted; core import and library API remain functional |
| `--track-io` supplied | Command reports the configured I/O instrumentation for the operation |
