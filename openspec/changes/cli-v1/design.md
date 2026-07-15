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
CLI should not invent a divergent "safer" story than the library unless we
consciously choose unzip-like overwrite (`RENAME`) for the demo wedge.

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

Overwrite: expose `--overwrite {error,skip,replace,rename}` once `RENAME` lands;
**default left open** (see Open Questions) — spec will say "default documented;
MUST be one of ERROR or RENAME" until chosen.

**Rejected:** full remote-control of every extract kwarg.

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

### 9. Implementation layout

`archivey/cli/` package: `main.py` (parser), `list.py`, `test.py`, `extract.py`,
`info.py`, shared formatting/filters. `argparse` only (stdlib). DEV's single
module is reference, not a port target.

### 10. Global options (v1)

`--password`, `--track-io`, `--version` (version + optional dependency matrix),
`-v`/`--verbose`, `--hide-progress` (or auto-disable when not a TTY). Member
patterns: see open question.

## Risks / Trade-offs

- [Default overwrite undecided] → Spec leaves an explicit TBD; implementation
  blocked on that one call for extract demos.
- [Visible `--salvage` that errors] → Slightly noisy; better than silent ignore
  teaching false safety.
- [Hybrid two ways to invoke] → Document aliases in `--help`; tests cover both.
- [info vs list overlap] → Keep info member-free; list path-free of format essay.

## Open Questions

### Must answer before extract ships (blocking)

1. **Default `--overwrite`:** `error` (library-aligned) vs `rename` (unzip-like
   wedge)? Recommendation lean: **`rename` for CLI only**, library stays `ERROR`
   — CLI is the demo of pleasant safe extract; library stays conservative.
2. **Extract destination:** positional `[dest]` vs required `--dest` vs default
   `.`? Lean: positional optional, default `.`.

### Should answer before locking the parser (important)

3. **Pattern syntax:** positional `archivey list a.zip '*.py'`, `--include`/
   `--exclude`, DEV-style `--` separator, or combination?
4. **Exit-code map:** rich (`0` ok, `1` usage, `2` open/format, `3` integrity,
   `4` extract policy rejections) vs simple `0`/`1`/`2`?
5. **Multi-archive:** `archivey list *.zip` supported in v1?
6. **`test` chatty vs quiet:** print per-member lines (DEV) or quiet + summary?
7. **Stdin / `-` archives** in v1?

### Can defer (hash / polish)

8. Hash v1 timing relative to CLI land (same change vs follow-up).
9. `--json` on `list`/`info` in a follow-up.
10. Whether `detect` should stay a pure alias or gain distinct magic-only
    defaults vs `info`.
