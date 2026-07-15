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

`list`, `test`, and `extract` SHALL support fnmatch member filters. Positional
patterns after the archive path SHALL act as **include** filters (a member is
selected when it matches any positional, or when no positional is given).
`--exclude PATTERN` (repeatable, long-form only — no short flag) SHALL remove
matching members; a member SHALL be processed when it matches an include (or none
is given) AND matches no `--exclude`. The system SHALL NOT provide a redundant
`--include` flag. `--track-io` SHALL report configured I/O instrumentation when
supplied. `--password` SHALL be accepted for encrypted archives.

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
`strict`). Destination SHALL be selected with `-d` / `--dest`; remaining
positionals after the archive path SHALL be member filters only (no bare
positional destination). When `-d` is given, extraction SHALL write into that
directory verbatim (`-d .` reproduces classic splatter-into-cwd behavior). When
`-d` is omitted, the destination SHALL default to a smart enclosing directory to
avoid tarbombs: extract into `./<archive-stem>/` when the archive has multiple
top-level entries; extract into `.` when the archive already has a single
top-level directory (no redundant nesting) or is a single-file/single-stream
archive. Container-name collisions SHALL be resolved by the overwrite policy.
Overwrite SHALL default to `rename` once `OverwritePolicy.RENAME` exists
(`--overwrite` may select `error` / `skip` / `replace` / `rename`).

#### Scenario: CLI behavior matrix

| Case | Expected |
| --- | --- |
| `archivey <archive>` | Same as `archivey list <archive>` |
| `archivey list <archive>` / `archivey -l <archive>` | Layer-1 member listing |
| `archivey list <archive> --digests` | Listing includes stored digests; no member body read for digests alone |
| `archivey test <archive>` / `archivey -t <archive>` | Fully reads members, verifies stored digests, reports failures |
| `archivey extract <archive>` / `archivey -x …` | Extracts under `--policy` default `strict`, overwrite default `rename`, into the smart default dest |
| `archivey extract <archive>` where archive has many top-level entries | Extracts into `./<archive-stem>/` (no cwd splatter) |
| `archivey extract <archive>` where archive has a single top-level dir | Extracts into `.`; reuses the archive's root dir (no redundant `foo/foo/`) |
| `archivey extract <archive> -d out/ '*.py'` | Dest is `out/` verbatim; `*.py` is a member filter |
| `archivey extract <archive> -d .` | Extracts into cwd verbatim (classic splatter, opt-in) |
| `archivey extract <archive> --policy trusted` | Maps to `ExtractionPolicy.TRUSTED` |
| Subcommand includes fnmatch pattern(s) after the archive | Operation limited to matching member names (positional = include) |
| `archivey extract <archive> '*.py' --exclude '*_test.py'` | Includes `*.py` minus `*_test.py`; exclude wins over include |
| `archivey <verb> <archive> --include …` | Usage error — `--include` is not provided (use a positional) |
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
