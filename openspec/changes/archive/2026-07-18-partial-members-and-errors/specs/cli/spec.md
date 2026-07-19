## MODIFIED Requirements

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
`--include` flag. Each invocation SHALL accept exactly **one** archive positional
(multi-archive is out of scope for this capability). `--password` SHALL be
accepted for encrypted archives; when an encrypted archive is opened, no
`--password` was supplied, and stdin is a TTY, the system SHALL prompt for the
password without echoing it.

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
`-v` / `--verbose` SHALL add a per-member OK/FAIL line.

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
| Subcommand includes fnmatch pattern(s) after the archive | Operation limited to matching member names (positional = include) |
| `archivey extract <archive> '*.py' --exclude '*_test.py'` | Includes `*.py` minus `*_test.py`; exclude wins over include |
| `archivey <verb> <archive> --include …` | Usage error — `--include` is not provided (use a positional) |
| `[cli]` extra absent / `tqdm` missing | Progress suppressed; command and library API remain functional |
| `--track-io` supplied | Reports decode/seek accounting (bytes decompressed, compressed bytes consumed, source seeks) via the measurement hook; no `builtins` patching |
| `archivey -x <archive>` (dash-prefixed verb) | Usage error — verbs are bare words (`x`), not options; `-x` is not a mode selector |

### Requirement: exit codes are minimal and argparse-aligned

The system SHALL exit `0` on success and `2` on CLI usage errors (unknown
verb/flag or bad arguments — the argparse default). All operational failures
(unreadable, unsupported, or corrupt archive; read/integrity failure; extraction
error; incomplete listing whose `MemberListReport.error` is set) SHALL exit `1`
in this capability. Exit codes `≥3` SHALL be reserved and MUST NOT be emitted in
this change; documentation SHALL direct callers to treat any nonzero code other
than `2` as a failure and MUST NOT assume `1` is the only failure code.

#### Scenario: exit codes

| Case | Expected |
| --- | --- |
| `archivey list <valid-archive>` | Exit `0` |
| `archivey --badflag` / unknown verb | Exit `2` (usage) |
| `archivey list <corrupt-or-unreadable>` | Exit `1` |
| `archivey list <archive-with-recoverable-prefix-and-terminal-error>` | Exit `1` (after printing recovered members) |
| `archivey test <archive-with-failing-member>` | Exit `1` |
