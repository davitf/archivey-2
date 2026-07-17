# Brief 4 — CLI (PR #120, `cli-v1`): design + implementation review

Review of the `archivey` command as proposed in `openspec/changes/cli-v1/` (merged
in #110) and implemented in **PR #120** (`cursor/cli-v1-cdb2` @ `42c688d`). Covers
both the *decisions* (grammar, defaults, safety posture) and the *code*
(correctness, clarity, UX). Docs-only — no source is modified. All findings were
reproduced by running the CLI from the PR branch; line references are against
`42c688d`.

## Headline

**The design is strong and the implementation is close.** The grammar decisions
(known-verb-wins default-to-list, `-d`-only destination, anti-tarbomb smart dest,
positional includes + long-only `--exclude`, argparse-aligned exit codes, reserved
`-` / `--salvage` / write verbs, TTY password prompt, stdout/stderr hygiene) are
individually well-reasoned, consistent with each other, and correctly leave room
for `hash`/`create`/`convert`/salvage. The verb implementations are small, mostly
correct, and the behavior-matrix tests cover every scenario in the delta spec —
all 29 pass, and `openspec validate --strict cli-v1` is clean.

The gap is between the spec's floor and the PR's own ambitions. Two bugs are
user-facing today: **every global flag placed before the verb is silently
discarded** (F1 — `archivey --password secret t enc.7z` fails to decrypt,
`archivey --track-io a.zip` reports nothing, and the `--help` usage line
explicitly advertises this placement), and **piping `list` into `head` exits 1
with `[Errno 32] Broken pipe` noise** (F2 — the `except BrokenPipeError` handler
is dead code behind `except OSError`). Beyond those, the friendliness goals are
undercut by silence in exactly the places this CLI's defaults make surprising:
the default `--overwrite rename` renames collided files **without telling the
user** (F3), and `extract -v` does nothing at all (its `verbose` is `del`'d).

Baseline for this review: `uv sync --group dev --extra all` on the PR worktree,
Python 3.11.15; `pytest tests/test_cli.py` → 29 passed.

## Design review

### Endorsed as-is (no change requested)

- **Known-verb-wins + default-to-list** (Decision 1). The escape hatch
  (`archivey list ./x`) is tested, the default verb is read-only, and reserved
  write verbs are reachable only by explicit name. Right call, correctly argued.
- **`-d`/`--dest` only, no positional dest** (3b) and **smart enclosing dir**
  (3c). The `unar`-style rule with the single-root exception and `-d .` opt-out
  is the best available shape for the "safer unzip" story. Implementation
  correctly avoids `foo/foo/` double-nesting (verified).
- **Positional includes + long-only `--exclude`** (11); **one archive per
  invocation** (13); **quiet `test` + `-v`** (14); **exit codes 0/1/2 with ≥3
  reserved** (12); **`-` reserved** (15); **argparse over Click** (9) given the
  zero-dep + always-installed constraints; **`--track-io` via
  `enable_measurement()`** with no builtins patch (10) — implemented exactly as
  specified, counters read via a first-party `isinstance(BaseArchiveReader)`.
- **CLI `rename` vs library `ERROR` overwrite split** (3). The split itself is
  right — but see F3: a *silent* rename default is only defensible if renames
  are reported.
- The **`has_static_candidates` relaxation** in `core.py:218` (provider-only
  passwords no longer rejected on non-encrypted formats) is correct, narrowly
  scoped, tested at both the library (`test_tar.py`) and CLI level, and does not
  contradict any spec requirement (the old guard was implementation-level, not
  spec'd).
- The **ZIP ASCII-sniff diagnostic fix** (`zip_reader.py:503-533`) riding along
  in this PR is correct (all paths in the `except` return; no unbound-variable
  fallthrough) and well tested.

### Design-level gaps (decisions to make, not bugs) — see QUESTIONS.md

- **D1. Smart dest ignores the member filters.** `smart_dest` computes top-level
  entries from *all* members, so `archivey x a.zip 'b/*'` lands in `./a/b/…`
  even though the filtered set has the single root `b/`. Computing tops on the
  filtered set gives `./b/…`, matching the single-root rule's spirit.
- **D2. Extraction outcome reporting.** `extract_all` returns an
  `ExtractionReport` (per-member status incl. `requested_path != path` rename
  markers); the CLI drops it on the floor. Decide the default verbosity:
  recommended — one summary line to stderr always (`N extracted, M renamed, K
  skipped`), per-member lines + rename details under `-v`.
- **D3. `test` abort-vs-continue on member-open failure** (see F4) — spec says
  "reports failures", but an open-time failure aborts with no summary.
- **D4. Logging/diagnostics ownership.** The CLI configures no logging; library
  warnings (e.g. `Member name normalized: 'sub' -> 'sub/'`) reach the terminal
  via Python's bare last-resort handler on every `list`/`test`. Decide the
  CLI's logging posture (default WARNING to stderr with a real formatter; `-v`
  or `--debug` raises verbosity; maybe `-q`).
- **D5. Progress is extract-only.** `test` on a multi-GB archive shows nothing
  until the summary. tqdm plumbing exists; a byte-progress bar on `test` is
  cheap and matches DEV behavior.
- **D6. Future-verb hazard of known-verb-wins.** Every verb added later (e.g.
  `cat`, `ls`) silently changes the meaning of `archivey <that-word>` for files
  with that name. Consider reserving the plausible set now (as `hash`/`create`/
  `convert` already are) and noting the policy in the spec.
- **D7. Exit-code flavor consistency for reserved surface.** Reserved *verbs*
  exit 2 (usage) but reserved `--salvage` and the `-` token exit 1 (via
  `CliError` default). All three are "you asked for a thing that doesn't exist
  yet"; pick one flavor (2 feels right for all).

## Findings

| # | Sev | Where | One-liner |
|---|-----|-------|-----------|
| F1 | **High** | `cli/main.py:130-227` | Global flags before the verb are parsed by the main parser, then **silently clobbered by the subparser's defaults** (shared-`parents` argparse pitfall). `--password`/`--track-io`/`-v`/`--hide-progress` all affected; hits the flagship default-list form (`archivey --track-io a.zip`) and the placement shown in `--help`'s own usage line. |
| F2 | **High** | `cli/main.py:318-325` | `except BrokenPipeError: return EXIT_OK` is dead code — `BrokenPipeError ⊂ OSError` and the `except OSError` clause precedes it. `archivey l big.zip \| head -1` exits **1** and prints `[Errno 32] Broken pipe` to stderr (reproduced). |
| F3 | **Medium** | `cli/extract_cmd.py:91,116-133` | Default `--overwrite rename` renames collided files (`a.txt` → `a (1).txt`) with **zero output**; the returned `ExtractionReport` (which records every rename/skip) is discarded, and `extract`'s `verbose` is `del`'d — `-v` is a no-op on the verb where it matters most. |
| F4 | **Medium** | `cli/test_cmd.py:36-55` | The per-member `try` wraps only `stream.read()`; a member whose stream fails to **open** (encrypted member + wrong/absent password, corrupt local header) raises during iterator advance, aborting `test` with no `N OK, M failed` summary (reproduced with an AES 7z). Mid-read digest failures correctly count-and-continue; open failures should too where the format allows. |
| F5 | **Low+** | `cli/main.py:131-227` | `allow_abbrev` is left on, and abbreviations interact badly with `_inject_default_list`: `archivey --pass secret a.zip` becomes `--pass list secret a.zip` → argparse sets `password="list"` then fails with `invalid choice: 'secret'` (reproduced — baffling message, wrong-password near-miss). `allow_abbrev=False` on all parsers also keeps the `_VALUE_OPTIONS` table honest. |
| F6 | **Low** | `cli/main.py:50-73` | `_inject_default_list` treats bare `-` as an option (`startswith("-")`), so `archivey -` exits 2 with `invalid choice: '-'` instead of the friendly reserved-stdin message that `archivey list -` gives. Special-case `-` as a positional in the scan. |
| F7 | **Low** | `cli/main.py:285-325` | No `KeyboardInterrupt` handling: Ctrl-C mid-extract spews a raw traceback. Catch → return 130 (and consider a partial-output note on extract). |
| F8 | **Low** | `cli/extract_cmd.py:20-36,49-72` | `_archive_stem` edge cases: a file named exactly `.tar.gz` yields stem `""` → `Path("")` == cwd → splatter despite multiple tops; the hardcoded suffix list misses `.tar.Z`/`.tar.lzma`/`.taz`/`.tzst`/… Simpler and complete: strip the last suffix, then a remaining `.tar`; or use `reader.format.file_extension()` (already available at the call site). |
| F9 | **Low** | `tests/test_cli.py:259-268` | `test_no_tqdm_progress_still_extracts` monkeypatches `progress_mod.make_progress_callback`, but `extract_cmd` imported the function by name at module import — the patch never takes effect; the test passes vacuously (non-TTY already yields `None`). Patch `archivey.cli.extract_cmd.make_progress_callback` instead. |
| F10 | **Low** | `cli/extract_cmd.py:124-132`; `cli/main.py:321` | An `OSError` during extraction (disk full, perms) bypasses the `ArchiveyError` handler and its "extraction stopped; remaining members were not extracted" explanation — the user gets a bare errno line with no stop notice. Catch `OSError` in the same clause. |
| F11 | **Low** | `cli/progress.py:49-83` | Bar lifecycle: never closed when `members_total is None` (streaming formats) or when extraction aborts (no `try/finally`), relying on GC at process exit; fine today, fragile if `main()` is embedded. Also `_display_stream`'s fallback writes to `sys.__stderr__` even when the *process* stderr was deliberately redirected by a wrapper that replaced `sys.stderr` — intended for test runners, but surprising as library-ish behavior. |
| F12 | **Nits** | various | (a) `info` output alignment is ragged (`confidence:certain`, `multivolume:False` vs padded keys) and prints enum reprs (`ArchiveFormat.ZIP`) instead of human names; (b) `track-io` prints `compressed_bytes_consumed=None` — print `-`; (c) container rename uses `stem-1` while the library's file rename yields `name (1).txt` — two styles in one tool; (d) `test` counts directories/links in `N OK` (inflates vs `unzip -t` expectations); (e) `fnmatch.fnmatch` is case-insensitive on Windows — `fnmatch.fnmatchcase` gives deterministic cross-platform filter semantics; (f) `main(out=…)`'s `out` is `del`'d and verbs ignore `main`'s `err` — the params suggest an injection seam that doesn't exist; wire them or drop them; (g) `--track-io` on `info` is silently `del`'d — either report (open does decode work for some formats) or say "n/a". |

## F1 detail + verified fix shape

`build_parser()` passes the *same* `_common_parent()` instance to the main parser
and to every subparser. On 3.11/3.12 (and current 3.13), `_SubParsersAction`
re-applies the subparser's defaults to the shared namespace, so values the main
parser already parsed are overwritten:

```text
archivey --password secret test enc.7z   → password=None   (prompt/fail; silent)
archivey --track-io a.zip                → track_io=False   (flag no-ops; silent)
archivey -v test a.zip                   → verbose=False
```

Verified fix (ran against this branch): build **two** parent instances —
the top-level one with real defaults, the subparser one with
`default=argparse.SUPPRESS` on every option — so an absent post-verb flag sets no
attribute and cannot clobber. Note the trap that makes the obvious one-parent
`set_defaults` variant fail: `parents=` copies **action references**, so
`main.set_defaults(...)` mutates the shared actions' defaults back. With the
two-instance shape, post-verb still overrides pre-verb (`--password early test
--password late` → `late`), which is the right precedence. Add pre-verb placement
tests for all four globals.

## Spec compliance

Every scenario row in `openspec/changes/cli-v1/specs/cli/spec.md` was exercised
(via `tests/test_cli.py` plus manual runs): default-list dispatch, verb-named
file, reserved verbs, aliases, `--digests`, smart dest (multi-top, single-root,
`-d .`), include/exclude precedence, `--include` rejected, exit codes, `-`
reserved on named verbs, no-tqdm degradation, `--track-io` accounting. No spec
violations found; F1 lives in territory the spec doesn't pin (it never says
globals work pre-verb) but the implementation's own help advertises it, so it is
judged against the PR, not the spec. `openspec validate --strict cli-v1` passes;
`tasks.md`-only delta vs #110 confirmed (`proposal.md`/`design.md`/`specs/*`
byte-identical to main).

## Suggested fix order

1. F1 (two-instance parents + SUPPRESS; pre-verb tests) — silent wrong behavior.
2. F2 (reorder handlers; suppress the late stderr flush noise) — every pipeline.
3. F3 + D2 (consume `ExtractionReport`; summary line; wire `-v`) — the safety
   story depends on renames being visible.
4. F4/D3 (count open-failures as FAIL and continue where the iterator allows).
5. F5–F12 as cleanup in one pass; D1/D4–D7 after maintainer calls in
   QUESTIONS.md.
