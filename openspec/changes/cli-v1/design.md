## Context

The `cli` capability today is a thin matrix (`list` / `test` / `extract`, fnmatch,
`--track-io`, tqdm behind `[cli]`). No implementation exists in v2; DEV's
`archivey.internal.cli` is the reference UX (flag modes, default=`test`, human
ls-ish lines, SHA-256 while verifying, `--password`, `--dest`, overwrite modes,
`--track-io` via monkeypatched `open`).

Release sequencing: land after `cross-platform-name-safety` so extract can offer
`OverwritePolicy.RENAME`. Writing/`convert` are post-1.0; salvage is a fast-follow
feature — both must not be blocked by CLI grammar choices.

## Goals / Non-Goals

**Goals:**
- Subcommand grammar (bare-word verbs + single-letter aliases) with **default verb = list**
- First-cut verbs: `list`, `test`, `extract`, `info` (detect + archive identity)
- Expose `ExtractionPolicy` on extract; keep other extract knobs minimal
- Layered `list` output: human layer-1 default; stored digests opt-in
- Reserve `--salvage` and room for `hash` / `create` / `convert` without implementing them
- Stdlib-only CLI core; `tqdm` optional via `[cli]`

**Non-Goals:**
- Implementing salvage, hashing emit formats, create/convert, or `cat`
- Exposing every library extract knob (`OnError`, custom filters, full cost API)
- Config files, shell completion, TUI
- Freezing a JSON schema for `ArchiveMember` in this change (may add later)

## Investigations

### DEV CLI (reference)

| Aspect | DEV behavior | Keep / change |
| --- | --- | --- |
| Mode shape | Flags `-l`/`-t`/`-x`, default **test** | Bare-word subcommands + single-letter aliases (`l`/`t`/`x`/`i`); default **list** |
| Listing line | enc, size, mode, crc, sha16, mtime, name | Layer-1 without digests by default |
| Verify | Always compute SHA-256 + check stored CRC | `test` = stored-digest verify + full read; no mandatory SHA emit |
| Patterns | After `--` | Positional includes + `--exclude` (Decision 11) |
| Progress | tqdm | Same; degrade without `[cli]` |
| Track IO | Monkeypatch `builtins.open` | Prefer library cost/`--track-io` if available; avoid builtins patch if possible |
| Version | `--version` + dependency matrix | Keep |

### Library defaults (extract)

`ExtractionPolicy.STRICT` and `OverwritePolicy.ERROR` are the library defaults today.
CLI keeps **policy=strict** aligned with the library, but defaults **overwrite to
`rename`** for unzip-like demos — a deliberate CLI/library split (Decision 3).

## Decisions

### 1. Subcommand grammar (bare-word verbs, single-letter aliases); default = list

```
archivey [global] <archive> [patterns…]           → list
archivey [global] list|test|extract|info …        → named verb
archivey [global] l|t|x|i …                        → single-letter alias for the same verb
```

**Verbs are commands, not options: bare words, never dash-prefixed.** Each verb
has a full name (`list`) and a single-letter alias (`l`), both plain words;
short aliases are registered as argparse subparser aliases
(`add_parser("extract", aliases=["x"])`). Options always take a dash (`-d`,
`--policy`); verbs never do. This is the git / docker / cargo / **7z** model
(`7z x`, `7z l`) and it matches the "commands have no dash, options do" mental
model.

There is **no** dash-prefixed verb form: `archivey -x …` is not accepted (`-x`
would read as an option, and the whole point is that verbs aren't options). One
spelling per verb in each namespace — no tar-style accept-both. Because the
letters are subparser aliases rather than a separate flag layer, there is no
"alias vs explicit subcommand" conflict to resolve.

Bare `archivey` with no archive path prints help and exits non-zero (usage).

**Default-to-list dispatch (known-verb-wins).** The default verb makes the first
positional ambiguous — verb or archive path? The rule: if the first token is a
**registered verb or alias** — `list`/`l`, `test`/`t`, `extract`/`x`, `info`/`i`,
`detect`, plus the reserved `hash`/`create`/`convert`/`cat` — dispatch that verb;
otherwise treat the token as an archive path and run `list`. The reserved words
are load-bearing for parsing *before* they are implemented: `archivey create`
MUST error as "not yet" (Decision 7 / flag hygiene), not silently list a file
named `create`. `cat` is reserved now for the same reason (member-to-stdout is a
likely future verb). New verbs may be reserved or added later and take
precedence over same-named files; the escape hatch below is permanent.

The only casualty is a file whose name is exactly a verb word (`./x`, `./list`,
`./hash`). Escape hatch: name the verb explicitly — `archivey list x`,
`archivey l x`. This is acceptable because (a) the default verb is `list`, which
is **read-only** — the implicit path can never do anything destructive; the worst
case is an unwanted listing — and (b) the collision set is a handful of short
reserved words, easily worked around. Keeping `create` (a write verb) reachable
only through its explicit name, never through the implicit default, is deliberate.

**Rejected:**
- Flag-only mode selectors (DEV `-l`/`-t`/`-x`) — crowds write/convert later and
  blurs the command/option line (the tar ambiguity).
- Dash-prefixed aliases (`-x` = extract) *alongside* subcommands — the earlier
  hybrid draft; reopened and dropped for the bare-letter form above. Trades a
  little unzip muscle memory for a consistent, unambiguous grammar.
- Subcommand-only (no single-letter aliases) — worse daily ergonomics for the
  unzip audience; `x`/`l` recover the terseness without reintroducing dashes.
- Require an explicit verb always, no default (git / `7z` / docker print help on
  a bare argument) — cleaner grammar, but sacrifices the `archivey foo.zip`
  ten-second-inspect wedge. Kept the default because `list` is read-only, so the
  implicitness is safe (see dispatch rule above).

### 2. Verbs in v1 vs reserved

| Verb | v1 | Notes |
| --- | --- | --- |
| `list` | yes | Default |
| `test` | yes | Integrity / full-read verify |
| `extract` | yes | Safe extract + `--policy` |
| `info` | yes | Format detection + archive identity (alias: `detect` → same command) |
| `hash` | reserved | Help mentions "not yet"; no implementation required to ship CLI |
| `create` / `convert` | reserved | Grammar must not consume `-c` for "check" |

`detect` is an alias of `info` (magic + open summary), not a separate code path,
unless `--detect-only` is passed (magic/sniff without full member materialization
when feasible).

### 3. Extract policy surface

Expose `--policy {strict,standard,trusted}` mapping 1:1 to `ExtractionPolicy`.
**CLI default = `strict`** (matches library). Do not expose `OnError` or custom
member filters in v1.

Overwrite: expose `--overwrite {error,skip,replace,rename}` once `RENAME` lands.
**CLI default = `rename`** (unzip-like wedge). Library API stays `ERROR` —
intentional CLI/library split: the command is the pleasant demo; the library
stays conservative for programmatic callers.

**Rejected:** full remote-control of every extract kwarg.

### 3b. Extract dest vs filters (no competing positionals)

Positional `[dest]` plus positional patterns is ambiguous:

```
archivey extract a.zip out        # dest or filter named "out"?
archivey extract a.zip '*.py'     # filter — clear only by luck
archivey extract a.zip out '*.py' # still guessing which is dest
```

Heuristics (exists-as-dir, trailing `/`) are fragile and platform-surprising.

**Decision: destination is always `-d` / `--dest`.** Remaining positionals after
the archive are member filters only:

```
archivey extract archive.zip
archivey extract archive.zip -d out/
archivey extract archive.zip -d out/ '*.py' 'docs/*'
archivey extract archive.zip '*.py'          # smart default dest (see 3c)
```

Same `-d`/`--dest` shape as DEV and `unzip -d`. "Optional dest" means *omit the
flag* (smart default, 3c), not *optional positional*.

**Rejected:** bare positional dest; trailing-slash heuristics; requiring `--`
before patterns as the only disambiguator (still fine as an *additional*
include form later).

### 3c. Default destination when `-d` is omitted (anti-tarbomb)

The dest *mechanism* (3b) is settled; the dest *default* is separate. A default
of literal `.` re-inherits the single worst `unzip`/`tar`/`7z` footgun: a
**tarbomb** — an archive with many loose top-level entries splatters them across
the current directory. archivey's pitch is "the safer unzip," so splattering cwd
by default contradicts the reason the command exists.

The user-friendly extractors converged on a **smart enclosing directory** rule:
`unar` (creates a dir "if there is more than one top-level file or folder …
helps prevent tarbombs"), `dtrx`, `aunpack`, and every GUI extractor (Ark, File
Roller, macOS, Explorer "Extract All"). archivey adopts the same rule.

**Decision:** when `-d` is omitted, derive the destination from the archive and
avoid double-nesting:

- **Multiple top-level entries** → extract into `./<archive-stem>/` (the
  container that prevents the splatter).
- **Single top-level directory** → extract into `.`; that existing root dir is
  already the container, so wrapping it would only create redundant `foo/foo/`.
- **Single-file / single-stream archives** (`.gz`, `.bz2`, `.xz` of one file) →
  write the decompressed file into `.` with no wrapper dir (matches `gunzip`
  expectations).
- **Tops are computed on the filtered member set** when a cheap index exists
  (ZIP/7z/RAR). So `archivey x a.zip 'b/*'` that only extracts under `b/` reuses
  `.` rather than wrapping in `./a/`.
- **No cheap index** (plain TAR; future stdin) → **always** extract into
  `./<archive-stem>/` (no pre-extract listing pass), then **hoist** if the
  wrapper ends with exactly one top-level entry (file or directory) — recovers
  single-root reuse and filter-aware D1 without an index. Collision during hoist
  follows the overwrite policy (`rename` → `name (N)`; `skip`/`error` leave the
  wrapper).
- **Container-name collision** (`./foo/` already exists) → resolved by the
  overwrite policy; default `rename` (Decision 3) yields `foo (1)/`, `foo (2)/`, ….

When `-d DIR` **is** given, extract straight into `DIR` verbatim — no smart
wrapping. Explicit dest means "I know what I want," including `-d .` which
reproduces exactly the classic splatter-into-cwd behavior for anyone who wants
it. That makes a separate `-C`/`--cwd` "splatter" flag redundant.

```
archivey extract archive.zip            # → ./archive/ (or reuse single root)
archivey extract archive.zip -d out/    # → out/ verbatim
archivey extract archive.zip -d .       # → cwd, classic tar/unzip splatter
```

**Rejected:** default dest `.` (the tarbomb footgun); a dedicated `-C`/`--cwd`
flag (`-d .` already covers it).

### 4. List output layers

Default (layer 1): type, size, mtime, mode, encrypted flag, name; link target for
links. **No digests in the default view.**

Opt-in:
- `--digests` — show stored digests from `member.hashes` (no body read)
- `-v` / `--verbose` — attach member/archive diagnostics (and maybe compression
  method / solid hints when cheap)

**Deferred:** `--json`, CSV, and computed-hash columns (belong with `hash`).

### 5. `test` meaning

`test` fully reads selected file members and verifies **stored** digests through
the shared verification path. Formats without stored digests still "pass" if every
selected member is fully readable without error (TAR case). Does **not** emit
sha256sum-style output (that's `hash` later).

Human summary on stderr or end of stdout: counts ok/failed; non-zero exit on
failure.

### 6. `info` / `detect`

One command, two names. Prints: detected/opened format, path, backend identity
as available, volume/solid hints when known, encryption-at-header if known,
`list_supported_formats`-adjacent "why can't I open this" only on failure.
Does not list every member (that's `list`).

### 7. `--salvage` reserved

Accepted on `list`, `test`, `extract`, `hash` (when hash exists), `convert`
(when convert exists). In v1: if passed, exit with a clear "not implemented"
error (or ignore only if we hide it — prefer **visible reserved flag** that
errors so scripts don't assume behavior). No partial salvage semantics.

### 8. Packaging: command always installed; tqdm optional

Console script `archivey` + `python -m archivey` always available from the base
package. `[cli]` continues to mean **tqdm for progress**; missing tqdm suppresses
progress (no hard failure), matching the existing packaging row ("progress
output") and the current cli scenario about core remaining functional.

**Rejected:** requiring `[cli]` to get the command at all (blocks the ten-second
demo).

### 9. Implementation layout + CLI framework

`archivey/cli/` package: `main.py` (parser), `list.py`, `test.py`, `extract.py`,
`info.py`, shared formatting/filters. DEV's single module is reference, not a
port target.

**Framework: stay on stdlib `argparse` + optional `tqdm`.** A short comparison
(not a multi-day spike) is enough — the packaging constraint decides it:

| Option | DX | Dep impact on `pip install archivey` | Fits zero-dep core? |
| --- | --- | --- | --- |
| `argparse` + optional tqdm | Verbose, adequate for 4 verbs | none (tqdm only via `[cli]`) | yes |
| Click / Typer | Better subcommands, `CliRunner`, nesting for create/convert later | Click/Typer become **core** deps if the command always ships | **no** — conflicts with packaging "no third-party when no extras" |
| Click only behind `[cli]` | Nice DX | Command gated on extra | Conflicts with "command always installed" demo goal |
| Rich / rich-click | Pretty help | more core or `[cli]` weight | same tension |

Click would be worth it if we dropped either zero-dep core *or* always-on
entry points. We are keeping both, so argparse wins. Structure the package so a
future swap is localized to `cli/main.py` (thin parser façade) — no need to
explore further unless packaging policy changes.

**Rejected:** Click/Typer as v1 default; a longer framework bake-off.

### 10. Global options (v1)

`--password` (see Decision 16), `--version` (version + optional dependency
matrix), `-v`/`--verbose`, `--hide-progress` (or auto-disable when not a TTY).
Member patterns: see Decision 11.

**`--track-io` — backed by the `benchmark-gate` measurement hook (resolved).** DEV
implemented it by monkeypatching `builtins.open`; v2 MUST NOT. The hook now exists
(landed in `benchmark-gate`, PR #100): `archivey.internal.measurement.enable_measurement()`
is a contextvar-gated, zero-overhead-when-off switch, and `BaseArchiveReader` exposes
`bytes_decompressed`, `source_seek_count`, and `compressed_bytes_consumed`.

**Decision:** keep `--track-io` in v1, implemented by wrapping the operation in
`enable_measurement()` and reading those counters off the reader — no `builtins`
patch. Two constraints:
- What it reports changes from DEV: not OS-level opens, but **bytes decompressed,
  compressed bytes consumed, and source seek count**. Reframe the help/output
  accordingly (it is a decode/seek accounting view, arguably more useful than
  open-counting).
- Those counters are **internal / not on the public `ArchiveReader` ABC** (a
  deliberate choice in PR #100 — "no public performance API"). The CLI reads them
  as a first-party internal consumer (`isinstance(reader, BaseArchiveReader)` /
  guarded `getattr`, to keep pyrefly/ty clean). The flag does **not** promote them
  to the public library surface, so PR #100's intent is preserved. Treat
  `--track-io` as a maintainer/debug global, not a headline end-user feature.

### 11. Member filters: positional include + `--exclude`

Positionals after the archive path are **include** filters on `list` / `test` /
`extract` (a member is selected when it matches any positional, or when there are
none = all). A positional pattern *is* the include list, so a separate
`--include` flag would just be a second spelling of the same thing.

The one thing positionals cannot express is subtraction ("everything except
`*.log`"). So add **`--exclude PATTERN` (repeatable)** and skip `--include` as
redundant. This is the standard archive-tool split: unzip (`-x`), tar
(`--exclude=`), 7z (`-x!`), rar (`-x`) all pair positional includes with a
dedicated exclude, and none ships a bare `--include`.

Selection rule: a member is processed when it matches an include (or none given)
**and** matches no `--exclude` — exclude wins over include, as in tar/unzip.

**Short form:** `--exclude` is **long-only** (no short flag). The classic short
spelling `-x` is unavailable here — `x` is `extract`'s verb alias (Decision 1) —
and tar hits the identical `-x`-is-extract collision and resolves it the same
way, by keeping `--exclude` long-form. No letter needs reassigning.

**Reserved (not v1):** `--include-from` / `--exclude-from FILE` (tar's `-T` /
`-X`) — reading big pattern lists from a file is the one place a flag genuinely
beats positionals; add on demand.

**Rejected:** `--include` (redundant with positionals); a short `-x`/`-e` for
exclude (collides with extract; long-only is clearer).

### 12. Exit codes (minimal, argparse-aligned)

The drafted "rich" map (`1` usage, `2` open/format, `3` integrity, `4` policy)
has a fatal flaw: **argparse itself exits `2` on argument errors**, so `2` is
already "CLI usage error" and cannot mean "open/format." Rather than fight the
framework, adopt the standard Unix shape:

| Code | Meaning |
| --- | --- |
| `0` | Success |
| `1` | Operation failed — bad/unsupported/corrupt archive, read error, integrity failure, extraction error |
| `2` | CLI usage error (unknown verb/flag, bad args — argparse default) |
| `≥3` | **Reserved.** v1 does not emit them. Callers MUST treat "nonzero and not `2`" as failure and MUST NOT assume `1` is the only failure code. |

**Reserved for near-term:** `3` = *extraction refused by safety policy*
(path traversal, unsafe link, quota) — a genuinely useful distinction for the
"safer unzip" story (a wrapper can tell "I blocked an attack" from "disk full").
v1 folds it into `1`; splitting it out is a compatible later addition because
callers are told not to assume `1` is exhaustive.

**Rejected:** the rich `1`=usage / `2`=format map (collides with argparse).

### 13. One archive per invocation in v1

`list *.zip` is tempting but collides with Decision 11: positional patterns are
*includes*, so `archivey list a.zip b.zip '*.py'` cannot be disambiguated —
where do archives end and patterns begin? Multi-archive also muddies extract
(each archive into its own smart-dest? all into one `-d`?).

**Decision: exactly one archive positional in v1.** A shell loop
(`for f in *.zip; do archivey l "$f"; done`) covers the batch case. Multi-archive
is deferred to a change that can design it with a `--` separator or flag-only
patterns, without breaking the include-positional grammar.

**Rejected:** multi-archive with positional includes (ambiguous); conditional
grammar that only allows patterns when a single archive is given (surprising).

### 14. `test` is quiet by default, `-v` is chatty

**Decision:** default `test` prints only failures plus a one-line summary
(`N OK, M failed`) to **stderr**, and exits non-zero if any member fails.
`-v`/`--verbose` adds a per-member `OK`/`FAIL` line (DEV / `unzip -t` behavior).

Silence-on-success matches Unix norms (`gzip -t`) and keeps stdout clean for
pipelines; `-v` gives humans the reassuring per-file trace. Consistent with the
global `-v` = "more detail" meaning already used by `list`.

**Rejected:** per-member lines by default (noisy, not script-friendly); total
silence even for the summary (less friendly for the interactive audience).

### 15. Stdin / `-` deferred; `-` reserved

Streaming an archive from stdin is real but format-dependent: ZIP and 7z keep
their directory/metadata at the *end*, so a non-seekable pipe forces spooling the
whole input to a temp file — and unbounded spooling is itself a safety concern
(memory/disk exhaustion) that deserves a deliberate design.

**Decision: no stdin archives in v1.** `-` is **reserved** to mean stdin and
SHALL error "stdin not supported yet" rather than open a literal file named `-`.
This keeps the token free for a later change that specifies spool-to-temp with a
size cap.

**Rejected:** silent best-effort stdin (would need hidden buffering with no
bound); treating `-` as a normal path (blocks the future meaning).

### 16. `--password` handling (avoid argv exposure)

`--password VALUE` is convenient but visible in `ps` and shell history — a poor
default for a safety-conscious tool.

**Decision:** keep `--password` for scripts, but when an encrypted archive is
opened and no password was supplied **and** stdin is a TTY, **prompt
interactively** (`getpass`, no echo). Document the argv-exposure caveat in help.
Reserve `--password-file` / an env var for a later change.

**Rejected:** `--password` as the only channel (forces secrets into argv);
prompting always (breaks non-interactive scripts).

### 17. Output stream hygiene

**Decision:** command *data* (listings, info summaries) goes to **stdout**;
progress bars, human summaries, prompts, and diagnostics go to **stderr**, so
`archivey l a.zip | …` pipes clean structured output. `tqdm` is imported lazily
(only when a bar is actually shown) so the `core-only` import path never touches
it, satisfying the packaging contract.

## Risks / Trade-offs

- [CLI default overwrite ≠ library] → Document clearly; demos use rename, scripts
  using the library still get ERROR unless they opt in.
- [Visible `--salvage` that errors] → Slightly noisy; better than silent ignore
  teaching false safety.
- [Verb + single-letter alias] → Both are bare words (`extract`/`x`); document in
  `--help`; tests cover both. Dropping unzip's `-x` muscle memory is the accepted
  cost of a consistent command/option split.
- [info vs list overlap] → Keep info member-free; list path-free of format essay.
- [argparse verbosity] → Acceptable; thin `main.py` keeps a future Click swap local.

## Open Questions

### Parser-locking questions — all resolved

1. **Pattern syntax** — Decision 11 (positional includes + `--exclude`).
2. **Exit codes** — Decision 12 (`0`/`1`/`2`, argparse-aligned; `≥3` reserved).
3. **Multi-archive** — Decision 13 (one archive per invocation in v1).
4. **`test` verbosity** — Decision 14 (quiet + summary; `-v` per-member).
5. **Stdin / `-`** — Decision 15 (deferred; `-` reserved).

### Needs a maintainer call (not parser-blocking)

A. **`--track-io` (Decision 10):** *Resolved.* The `benchmark-gate` measurement
   hook (PR #100) backs it — `enable_measurement()` + `BaseArchiveReader`
   counters. Kept in v1, no monkeypatch; reports decode/seek/compressed counts.
B. **Exit code `3` (Decision 12):** ship a distinct "refused by safety policy"
   code in v1, or fold into `1` for now? Recommend fold-into-`1`; splitting later
   is compatible.

### Deferred (hash / polish)

6. Hash v1 timing relative to CLI land (same change vs follow-up).
7. `--json` on `list`/`info` — highest-value deferred item (json is stdlib, so
   cheap), but needs a stable member schema, which this change non-goals. Follow-up.
8. `detect` stays a pure alias of `info` in v1; revisit distinct magic-only
   defaults only on demand.
