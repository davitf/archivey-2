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
- Hybrid CLI grammar (subcommands + short aliases) with **default verb = list**
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
| Mode shape | Flags `-l`/`-t`/`-x`, default **test** | Hybrid; default **list** |
| Listing line | enc, size, mode, crc, sha16, mtime, name | Layer-1 without digests by default |
| Verify | Always compute SHA-256 + check stored CRC | `test` = stored-digest verify + full read; no mandatory SHA emit |
| Patterns | After `--` | Prefer subcommand-native patterns; see open Q |
| Progress | tqdm | Same; degrade without `[cli]` |
| Track IO | Monkeypatch `builtins.open` | Prefer library cost/`--track-io` if available; avoid builtins patch if possible |
| Version | `--version` + dependency matrix | Keep |

### Library defaults (extract)

`ExtractionPolicy.STRICT` and `OverwritePolicy.ERROR` are the library defaults today.
CLI keeps **policy=strict** aligned with the library, but defaults **overwrite to
`rename`** for unzip-like demos — a deliberate CLI/library split (Decision 3).

## Decisions

### 1. Hybrid grammar; default verb = list

```
archivey [global] <archive> [patterns…]           → list
archivey [global] list|test|extract|info …        → named verb
archivey [global] -l|-t|-x|-i …                   → alias for list|test|extract|info
```

Short aliases are mutually exclusive with an explicit subcommand. Bare
`archivey` with no archive path prints help and exits non-zero (usage).

**Rejected:** flag-only (DEV) — crowds write/convert later. Subcommand-only —
worse daily ergonomics for the unzip audience.

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
- **Container-name collision** (`./foo/` already exists) → resolved by the
  overwrite policy; default `rename` (Decision 3) yields `foo-1/`, `foo-2/`, ….

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

`--password`, `--track-io`, `--version` (version + optional dependency matrix),
`-v`/`--verbose`, `--hide-progress` (or auto-disable when not a TTY). Member
patterns: see Decision 11.

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
spelling `-x` is unavailable here — it is `extract`'s short form (see the alias
question) — and tar hits the identical `-x`-is-extract collision and resolves it
the same way, by keeping `--exclude` long-form. No letter needs reassigning.

**Reserved (not v1):** `--include-from` / `--exclude-from FILE` (tar's `-T` /
`-X`) — reading big pattern lists from a file is the one place a flag genuinely
beats positionals; add on demand.

**Rejected:** `--include` (redundant with positionals); a short `-x`/`-e` for
exclude (collides with extract; long-only is clearer).

## Risks / Trade-offs

- [CLI default overwrite ≠ library] → Document clearly; demos use rename, scripts
  using the library still get ERROR unless they opt in.
- [Visible `--salvage` that errors] → Slightly noisy; better than silent ignore
  teaching false safety.
- [Hybrid two ways to invoke] → Document aliases in `--help`; tests cover both.
- [info vs list overlap] → Keep info member-free; list path-free of format essay.
- [argparse verbosity] → Acceptable; thin `main.py` keeps a future Click swap local.

## Open Questions

### Should answer before locking the parser (important)

1. **Pattern syntax beyond extract:** *Resolved — see Decision 11.* Positionals
   after the archive are include filters on `list`/`test`/`extract`; add
   `--exclude` (long-only, repeatable); no `--include`.
2. **Exit-code map:** rich (`0` ok, `1` usage, `2` open/format, `3` integrity,
   `4` extract policy rejections) vs simple `0`/`1`/`2`?
3. **Multi-archive:** `archivey list *.zip` supported in v1?
4. **`test` chatty vs quiet:** print per-member lines (DEV) or quiet + summary?
5. **Stdin / `-` archives** in v1?

### Can defer (hash / polish)

6. Hash v1 timing relative to CLI land (same change vs follow-up).
7. `--json` on `list`/`info` in a follow-up.
8. Whether `detect` should stay a pure alias or gain distinct magic-only
   defaults vs `info`.
