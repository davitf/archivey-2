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

The system SHALL provide an `archivey` command whose verbs are **subcommands**:
each verb is a bare word (never dash-prefixed) with a single-letter bare-word
alias. When no verb is present the verb SHALL default to `list`. Verb dispatch
SHALL be **known-verb-wins**: if the first positional token is a registered verb
or alias (including reserved verbs `hash` / `create` / `convert` / `cat`), the
system SHALL dispatch that verb; otherwise it SHALL treat the token as an
archive path and run `list`. A file whose name equals a verb word SHALL be
reachable by naming the verb explicitly (e.g. `archivey list x`). New verbs MAY
be added later and take precedence over same-named files; the `list <path>`
escape hatch is permanent. Verbs MUST NOT be selectable via a
dash-prefixed option form (e.g. `-x` SHALL NOT mean `extract`); options always
take a dash, verbs never do. Progress output SHALL use
`tqdm` from `[cli]` when available; core MUST NOT depend on `tqdm`. The console
script and `python -m archivey` MUST be importable/runnable without installing
`[cli]` (progress suppressed if `tqdm` is absent).

Supported verbs in this capability:

| Verb | Alias | Role |
| --- | --- | --- |
| `list` | `l` | Inspect members (default verb) |
| `test` | `t` | Full-read integrity check |
| `extract` | `x` | Safe extraction |
| `info` | `i`, `detect` | Format detection + archive identity |

`list`, `test`, and `extract` SHALL support fnmatch member filters. Positional
patterns after the archive path SHALL act as **include** filters (a member is
selected when it matches any positional, or when no positional is given).
`--exclude PATTERN` (repeatable, long-form only — no short flag) SHALL remove
matching members; a member SHALL be processed when it matches an include (or none
is given) AND matches no `--exclude`. The system SHALL NOT provide a redundant
`--include` flag. When one or more include patterns are given, each pattern that
matches no member SHALL produce a stderr warning
(`warning: pattern matched no members: '…'`). When every include misses on
`extract` or `test`, the command SHALL exit `1` after the warning(s). On `list`,
the same warnings SHALL be emitted but the exit code SHALL remain `0` when the
archive otherwise listed successfully. On `extract`, when there is exactly one
unmatched include that names an existing directory or ends with `/`, the warning
SHALL include a hint `(did you mean -d PATTERN?)`. Each invocation SHALL accept
exactly **one** archive positional (multi-archive is out of scope for this
capability). `--password` SHALL be accepted for encrypted archives; when an
encrypted archive is opened, no `--password` was supplied, and stdin is a TTY,
the system SHALL prompt for the password without echoing it.

Command data output (member listings, info summaries) SHALL be written to
**stdout**; progress bars, human summaries, prompts, and diagnostics SHALL be
written to **stderr**.

`--track-io` SHALL report I/O accounting for the operation using the internal
measurement hook (decode/seek counters), without patching `builtins.open`. It is
a maintainer/debug affordance and MUST NOT add a public library performance API.

`list` SHALL obtain its member set via `ArchiveReader.members_report()` (or an
equivalent report path). It SHALL print a human layer-1 member view by default
(type, size, mtime, mode, encrypted flag, name; link target for links) for every
recovered member in the report and MUST NOT show digests unless `--digests` is
set (stored `member.hashes` only; no body read). When the report’s `error` is
set, `list` SHALL still print the recovered members, SHALL emit a short stderr
message naming the terminal archive error, and SHALL exit nonzero (`1`). `-v` /
`--verbose` SHALL surface diagnostics when present.

`test` SHALL fully read selected file members and verify stored digests through
the shared verification stage (including CRC32 and Blake2sp where supported).
Members with no stored digest SHALL count as OK when fully readable without
error. `test` MUST NOT require emitting computed content hashes. By default
`test` SHALL be quiet — printing only failures and a one-line summary
(`N OK, M failed`) to stderr — and SHALL exit non-zero if any member fails;
`-v` / `--verbose` SHALL add a per-member OK/FAIL line. When a cheap member
index is available and the stream ends before every selected file member has
been counted OK or failed (archive-wide error or solid/poisoned abort), the
summary SHALL append `, K not tested` where `K` is the untested remainder.

`extract` SHALL use safe-extraction defaults and SHALL expose
`--policy {strict,standard,trusted}` mapping to `ExtractionPolicy` (CLI default
`strict`). Destination SHALL be selected with `-d` / `--dest`; remaining
positionals after the archive path SHALL be member filters only (no bare
positional destination). When `-d` is given, extraction SHALL write into that
directory verbatim (`-d .` reproduces classic splatter-into-cwd behavior). When
`-d` is omitted, the destination SHALL default to a smart enclosing directory to
avoid tarbombs. When a cheap member index is available without a streaming scan
(ZIP / 7z / RAR central directory, etc.), tops SHALL be computed on the
**filtered** member set: extract into `./<archive-stem>/` when that set has
multiple top-level entries; extract into `.` when it already has a single
top-level directory (no redundant nesting) or the archive is a
single-file/single-stream archive. When no cheap index is available (plain TAR,
future stdin sources, …), the destination SHALL initially be `./<archive-stem>/`
(always wrap — no pre-extract metadata pass); after a successful extract, if
that wrapper contains exactly one top-level entry, the system SHALL hoist it to
the cwd and remove the wrapper. The hoist SHALL produce the same final layout
as extracting directly into the cwd: directories merge into existing
directories, and per-file collisions resolve by the overwrite policy (`rename`
derives the library's `name (N)` spelling; `replace` replaces only the
individual files being extracted; `skip` keeps the existing file). The hoist
MUST NOT delete pre-existing files or directories under any policy. A collision
the policy cannot resolve without deleting data (`error`, or a dir-vs-file
shape under `replace`/`skip`) SHALL stop the hoist, leave the unmoved remainder
under the wrapper, and exit nonzero — mirroring the failure a direct extraction
would have hit. A sole root sharing the wrapper's own name (`src.tar.gz`
containing `src/`) SHALL be flattened in place, not treated as a collision.
Container-name collisions SHALL be resolved by the overwrite policy.
Overwrite SHALL default to `rename` once `OverwritePolicy.RENAME` exists
(`--overwrite` may select `error` / `skip` / `replace` / `rename`).
`extract` SHALL pass `OnError.CONTINUE` by default so policy rejections and
per-member read failures are recorded (`blocked:` / `failed:` lines plus the
closing summary) and remaining members are still extracted where the stream
allows. `--stop-on-error` SHALL restore `OnError.STOP` for that invocation.
On an early stop (STOP path or always-stop limit), the system SHALL still
report how many members were written before the stop.

#### Scenario: CLI behavior matrix

| Case | Expected |
| --- | --- |
| `archivey <archive>` | Same as `archivey list <archive>` (first token is not a known verb → list) |
| `archivey ./x` where `x` is a file and also the `extract` alias | Dispatches `extract` (known-verb-wins); list the file via `archivey list ./x` |
| `archivey create <archive>` (reserved, unimplemented) | Usage error "not yet"; does not fall through to `list` |
| `archivey cat <archive>` (reserved, unimplemented) | Usage error "not yet"; does not fall through to `list` |
| `archivey list <archive>` / `archivey l <archive>` | Layer-1 member listing |
| `archivey list <archive>` with recoverable prefix + terminal archive error | Prints recovered members on stdout; stderr names the error; exit `1` |
| `archivey list <archive> --digests` | Listing includes stored digests; no member body read for digests alone |
| `archivey test <archive>` / `archivey t <archive>` | Fully reads members, verifies stored digests, reports failures |
| `archivey extract <archive>` / `archivey x …` | Extracts under `--policy` default `strict`, overwrite default `rename`, into the smart default dest |
| `archivey extract <archive>` where archive has many top-level entries | Extracts into `./<archive-stem>/` (no cwd splatter) |
| `archivey extract <indexed-archive>` where archive has a single top-level dir | Extracts into `.`; reuses the archive's root dir (no redundant `foo/foo/`) |
| `archivey extract <indexed-archive> 'b/*'` where filtered set has single root `b/` | Extracts into `.` (tops on filtered set); lands as `./b/…` |
| `archivey extract <no-index-archive>` (e.g. plain TAR) with a single top-level dir | Extracts into `./<stem>/` then hoists the single root to cwd |
| `archivey extract <no-index-archive>` with multiple top-level entries | Extracts into `./<archive-stem>/` (no hoist) |
| `archivey extract <archive> -d out/ '*.py'` | Dest is `out/` verbatim; `*.py` is a member filter |
| `archivey extract <archive> -d .` | Extracts into cwd verbatim (classic splatter, opt-in) |
| `archivey extract <archive> --policy trusted` | Maps to `ExtractionPolicy.TRUSTED` |
| `archivey extract <archive-with-traversal-and-safe-members>` | Safe members extracted; `blocked:` lines; exit `3` |
| `archivey extract --stop-on-error <archive-with-bad-member>` | Stops at first failure; reports members written before stop |
| Subcommand includes fnmatch pattern(s) after the archive | Operation limited to matching member names (positional = include) |
| `archivey extract <archive> out` where `out/` exists and matches no member | stderr warning with `(did you mean -d out?)`; exit `1` |
| `archivey extract <archive> '*.missing'` | stderr warning; exit `1` |
| `archivey list <archive> '*.missing'` | stderr warning; exit `0` |
| `archivey extract <archive> '*.py' --exclude '*_test.py'` | Includes `*.py` minus `*_test.py`; exclude wins over include |
| `archivey <verb> <archive> --include …` | Usage error — `--include` is not provided (use a positional) |
| `[cli]` extra absent / `tqdm` missing | Progress suppressed; command and library API remain functional |
| `--track-io` supplied | Reports decode/seek accounting (bytes decompressed, compressed bytes consumed, source seeks) via the measurement hook; no `builtins` patching |
| `archivey -x <archive>` (dash-prefixed verb) | Usage error — verbs are bare words (`x`), not options; `-x` is not a mode selector |

### Requirement: info and detect summarize archive identity

The system SHALL provide `archivey info` (alias `detect`) that reports detected
and/or opened format identity for a path without listing every member. It SHALL
be suitable for answering "what does archivey think this file is?" including
failure cases with a typed/clear error. After a successful open, `info` SHALL
print an `access:` line summarizing the archive's `CostReceipt` (listing /
member-access / stream axes) in human prose. With `-v` / `--verbose`, it SHALL
also print the raw cost axes (`listing`, `access_cost`, `stream`,
`solid_blocks`).

#### Scenario: info vs list

| Case | Expected |
| --- | --- |
| `archivey info <archive>` / `archivey detect <archive>` | Prints format/identity summary including `access:`; does not dump full member listing |
| `archivey info -v <indexed-zip>` | Includes `access: random (indexed)` and raw cost axes |
| Unreadable/unknown file | Non-zero exit; clear error (no stack trace by default) |
| `archivey list <archive>` | Member listing; not a substitute for info's format summary |

### Requirement: version reports package identity and optional format matrix

`--version` SHALL print `archivey <version>` and exit. With `-v` / `--verbose`,
it SHALL also print a `formats:` matrix from the registry availability API
(`list_known_formats` / `format_availability`), including missing-component
install hints when support is not full.

#### Scenario: version

| Case | Expected |
| --- | --- |
| `archivey --version` | One line: `archivey <version>`; exit `0` |
| `archivey --version -v` / `archivey -v --version` | Version line plus `formats:` availability matrix |

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

The system SHALL NOT reuse a verb letter that commonly means create/compress for
integrity checking (in particular `c` MUST NOT mean "check"; leave it for a
future `create`). Help text MAY mention `hash`, `create`, `convert`, and `cat`
as forthcoming without implementing them. `cat` SHALL be reserved now so a later
member-to-stdout verb does not silently change the meaning of
`archivey cat` for a same-named archive file.

#### Scenario: flag hygiene

| Case | Expected |
| --- | --- |
| `t` | Means `test` (integrity), not create |
| Unknown verb `hash` / `create` / `convert` / `cat` before implementation | Usage error naming the verb as unavailable (not a silent fallthrough to `list`) |

### Requirement: exit codes are argparse-aligned with a policy-refusal code

The system SHALL exit `0` on success and `2` on CLI usage errors (unknown
verb/flag or bad arguments — the argparse default). Operational failures
(unreadable, unsupported, or corrupt archive; read/integrity failure; member
extraction `FAILED`; incomplete listing whose `MemberListReport.error` is set)
SHALL exit `1`. When `extract` completes under continue-on-error with one or
more members `BLOCKED` by safety policy and no member `FAILED`, the system
SHALL exit `3` (refused by safety policy). Exit codes `≥4` SHALL remain
reserved. Documentation SHALL direct callers to treat any nonzero code other
than `2` as a failure and MUST NOT assume `1` is the only failure code.

#### Scenario: exit codes

| Case | Expected |
| --- | --- |
| `archivey list <valid-archive>` | Exit `0` |
| `archivey --badflag` / unknown verb | Exit `2` (usage) |
| `archivey list <corrupt-or-unreadable>` | Exit `1` |
| `archivey list <archive-with-recoverable-prefix-and-terminal-error>` | Exit `1` (after printing recovered members) |
| `archivey test <archive-with-failing-member>` | Exit `1` |
| `archivey test <indexed-archive>` when the member stream aborts early | Summary includes `K not tested` for the untested remainder; exit `1` |
| `archivey extract <archive-with-traversal-and-safe-members>` | Extracts safe members; prints `blocked:`; exit `3` |
| `archivey extract <archive-with-corrupt-member>` | Extracts recoverable members; prints `failed:`; exit `1` |
| `archivey extract --stop-on-error <archive-with-bad-member>` | Stops at first bad member; exit nonzero |

### Requirement: stdin archives are reserved, not supported in v1

The system SHALL treat `-` as a reserved token meaning "read archive from stdin"
and SHALL fail fast with a clear "not supported yet" message rather than opening a
filesystem entry literally named `-`.

#### Scenario: stdin reserved

| Case | Expected |
| --- | --- |
| `archivey list -` | Non-zero exit; message states stdin archives are not supported yet |
| `archivey extract -` | Same |
