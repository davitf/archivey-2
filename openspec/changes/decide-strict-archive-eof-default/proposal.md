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

- **Decision (LOCKED — Option F, "signal-aware default"):** split the TAR EOF diagnostic on
  the `observed_kind` signal `_verify_tar_eof` already computes, instead of on one monolithic
  bool (full option survey + rationale in `design.md`):
  - **Default (`strict_archive_eof=False`, unchanged):** `absent`/`short` trailer → warn
    (Phase 5 / GNU-tar compatible); **`nonzero` trailer → raise `CorruptionError`** — a
    high-confidence early-stop / silent-shorten that a conformant tar never produces.
  - **`strict_archive_eof=True`:** all three buckets escalate — `absent`/`short` →
    `TruncatedError`, `nonzero` → `CorruptionError` — for inventory / dedupe / validators.
  - Extract raises **after** writing every salvageable member (raise-at-end); no soft-extract
    report field.
- **Minor BREAKING:** genuinely-malformed (`nonzero`) tars change from warn to raise by
  default; the common trailer-less / `cat`-joined corpus is unaffected. `config.py` default
  and the `archive-reading` config signature are **unchanged**, so no `archive-reading` delta.
- **Not in scope:** implementing a native TAR header walker (post-v1, open-issues P3) — the
  structural fix that can eventually make the ambiguous `absent`/`short` residual (complete
  trailer-less vs. truncated-at-boundary) archivey's own decision; and a salvage / best-effort
  read mode (`IDEAS.md`) — the future escape for callers who want to read a `nonzero` tar
  without an exception.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `format-tar` — split the EOF diagnostic on `observed_kind`: `nonzero` raises
  `CorruptionError` regardless of the flag; `absent`/`short` warn by default and escalate to
  `TruncatedError` under strict; extract raises at end after salvageable writes.
- `documentation` — formats + Gotchas teach the new signal-aware default and the narrowed
  job of `strict_archive_eof=True` (escalate the ambiguous `absent`/`short` residual);
  post-v1 native ZIP/TAR limitations framed as “may improve later.”

CLI strict-EOF wedge (`archivey test` strict by default) is a cross-note for `cli-v1` (not
owned here). No `archive-reading` delta — config default/signature unchanged.

## Impact

- **Public API:** TAR `members()` / iteration now raise `CorruptionError` at end-of-pass on a
  `nonzero` trailer under the default config; extract raises at end after writing salvageable
  members. `ArchiveyConfig.strict_archive_eof` default is **unchanged** (`False`).
- **Modules:** `tar_reader._verify_tar_eof` (the behavior change lives here); tests under
  `test_tar.py` / `test_archivey_config.py` / `test_diagnostics.py`; user docs (`formats.md`,
  future Gotchas); CLI when present. `config.py` unchanged.
- **Tests:** new `nonzero`→`CorruptionError` default case; `absent`/`short` stay warn;
  strict-mode `absent`/`short`→`TruncatedError` vs `nonzero`→`CorruptionError`; `tar -b1` +
  trailing-padding regression (no false-positive `nonzero`).
- **No extras/deps.**
