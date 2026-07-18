## Why

`ArchiveyConfig.strict_archive_eof` defaults to `False`: a missing TAR end marker emits
`ARCHIVE_EOF_MARKER_MISSING` (WARNING) and the pass completes. That matches Phase 5’s
deliberate compatibility choice (trailer-less / `cat`-joined tars are common; GNU tar
warns). The same check is also the only backstop for stdlib `tarfile` treating a corrupt
*non-first* header as clean EOF — so inventory/dedupe sweeps can get a silently shortened
listing unless callers opt into strict mode. Gotchas triage (`docs/internal/open-issues.md`
P1) asked whether to flip the default; that is a product stance choice, not a small bugfix,
and should be decided explicitly before v1 docs teach the wrong story.

## What Changes

- **Decision (open):** pick a default / split / soft-fail policy for `strict_archive_eof`
  among the options in `design.md` (keep False; True everywhere; True for random-access
  only; keep False + Gotchas/CLI; True + soft extract). **BREAKING** if the library default
  becomes `True` (or RA-only True).
- **Specs / docs / CLI follow the chosen option** — provisional deltas below assume the
  recommended Option D (keep default False; teach + CLI strict path); replace them if
  another option wins.
- **Not in scope:** implementing a native TAR header walker (post-v1); that is the structural
  fix that can eventually distinguish “missing trailer” from “corrupt-shortened listing.”

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `format-tar` — document stdlib silent mid-archive EOF as the same diagnostic; default
  stays False under provisional Option D (replace if B/C/E wins).
- `documentation` — formats + Gotchas teach the opt-in (or the new default’s escape
  hatch); post-v1 native ZIP/TAR limitations framed as “may improve later.”

If Option B/C/E wins before apply: also modify `archive-reading` (config default and/or
RA vs streaming split) and possibly extract-report semantics (Option E). CLI strict-EOF
wedge is a cross-note for `cli-v1` (not owned here).

## Impact

- **Public API:** possibly `ArchiveyConfig.strict_archive_eof` default; extract/`members()`
  end-of-pass failure mode under Options B/C/E.
- **Modules:** `config.py`, `tar_reader._verify_tar_eof`, tests under `test_tar.py` /
  `test_archivey_config.py` / diagnostics; user docs (`formats.md`, future Gotchas); CLI
  when present.
- **Tests:** default-config expectations flip under B/C/E; Option D mostly docs + CLI +
  maybe one recipe test.
- **No extras/deps.**
