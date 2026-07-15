## MODIFIED Requirements

### Requirement: archivey command with list, test, and extract subcommands

The system SHALL provide an `archivey` command as a **hybrid** CLI: named
subcommands plus short mode aliases. When neither a subcommand nor a mode alias
is present, the verb SHALL default to `list`. Progress output SHALL use `tqdm`
from `[cli]` when available; core MUST NOT depend on `tqdm`. The console script
and `python -m archivey` MUST be importable/runnable without installing `[cli]`
(progress suppressed if `tqdm` is absent).

Supported verbs in this capability:

| Verb | Aliases | Role |
| --- | --- | --- |
| `list` | `-l`, `--list` | Inspect members (default verb) |
| `test` | `-t`, `--test` | Full-read integrity check |
| `extract` | `-x`, `--extract` | Safe extraction |
| `info` | `-i`, `--info`, `detect` | Format detection + archive identity |

`list`, `test`, and `extract` SHALL support fnmatch member filters (exact pattern
syntax deferred to the implementation design until the open parser question
closes). `--track-io` SHALL report configured I/O instrumentation when supplied.
`--password` SHALL be accepted for encrypted archives.

`list` SHALL print a human layer-1 member view by default (type, size, mtime,
mode, encrypted flag, name; link target for links) and MUST NOT show digests
unless `--digests` is set (stored `member.hashes` only; no body read). `-v` /
`--verbose` SHALL surface diagnostics when present.

`test` SHALL fully read selected file members and verify stored digests through
the shared verification stage (including CRC32 and Blake2sp where supported).
Members with no stored digest SHALL count as OK when fully readable without
error. `test` MUST NOT require emitting computed content hashes.

`extract` SHALL use safe-extraction defaults and SHALL expose
`--policy {strict,standard,trusted}` mapping to `ExtractionPolicy` (CLI default
`strict`). Overwrite handling SHALL be selectable once `OverwritePolicy.RENAME`
exists; the default overwrite value is documented in design until finalized as
either `error` or `rename`.

#### Scenario: CLI behavior matrix

| Case | Expected |
| --- | --- |
| `archivey <archive>` | Same as `archivey list <archive>` |
| `archivey list <archive>` / `archivey -l <archive>` | Layer-1 member listing |
| `archivey list <archive> --digests` | Listing includes stored digests; no member body read for digests alone |
| `archivey test <archive>` / `archivey -t <archive>` | Fully reads members, verifies stored digests, reports failures |
| `archivey extract <archive> [dest]` / `archivey -x …` | Extracts under `--policy` (default `strict`) and the chosen overwrite default |
| `archivey extract <archive> --policy trusted` | Maps to `ExtractionPolicy.TRUSTED` |
| Subcommand includes fnmatch pattern(s) | Operation limited to matching member names |
| `[cli]` extra absent / `tqdm` missing | Progress suppressed; command and library API remain functional |
| `--track-io` supplied | Reports configured I/O instrumentation for the operation |
| Mode alias combined with an explicit conflicting subcommand | Usage error |

## ADDED Requirements

### Requirement: info and detect summarize archive identity

The system SHALL provide `archivey info` (alias `detect`) that reports detected
and/or opened format identity for a path without listing every member. It SHALL
be suitable for answering "what does archivey think this file is?" including
failure cases with a typed/clear error.

#### Scenario: info vs list

| Case | Expected |
| --- | --- |
| `archivey info <archive>` / `archivey detect <archive>` | Prints format/identity summary; does not dump full member listing |
| Unreadable/unknown file | Non-zero exit; clear error (no stack trace by default) |
| `archivey list <archive>` | Member listing; not a substitute for info's format summary |

### Requirement: salvage flag reserved without behavior

The system SHALL accept `--salvage` on `list`, `test`, and `extract` (and on
future `hash` / `convert` when those verbs exist) but MUST NOT implement salvage
semantics in this change. Passing `--salvage` SHALL fail fast with a clear
not-implemented message so callers cannot assume best-effort reads.

#### Scenario: salvage reserved

| Case | Expected |
| --- | --- |
| `archivey list <archive> --salvage` | Non-zero exit; message indicates salvage is not implemented |
| `archivey extract <archive> --salvage` | Same |

### Requirement: reserved verbs do not collide with future write/hash UX

The system SHALL NOT use short options that commonly mean create/compress for
integrity checking (in particular `-c` MUST NOT mean "check"). Help text MAY
mention `hash`, `create`, and `convert` as forthcoming without implementing them.

#### Scenario: flag hygiene

| Case | Expected |
| --- | --- |
| `-t` | Means `test` (integrity), not create |
| Unknown verb `hash` / `create` / `convert` before implementation | Usage error naming the verb as unavailable (not a silent fallthrough to `list`) |
