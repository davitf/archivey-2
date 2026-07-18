## Why

VISION claim (3) wants recoverable members **and** an honest error on damaged
input. Option F made the TAR end-of-archive error honest (`CorruptionError` /
strict `TruncatedError`), but random-access `members()` / `__iter__` still
fail-closed and discard the recovered prefix; only streaming yield-then-raise
surfaces both. api-coherence **Q7** owns this gap: a uniform way to return a
partial listing together with a terminal archive error without silencing either
side and without publishing a partial cache as a complete listing.

## What Changes

- Add `members_report() -> MemberListReport` that **always returns** recovered
  members plus `error: ArchiveyError | None` and a diagnostic snapshot — the
  materializing dual of streaming yield-then-raise. Named to contrast with
  `members()` (list / complete-or-raise), not as a synonym (`list_members`
  rejected as ambiguous).
- Keep `members()` / `scan_members()` as **complete-or-raise** (no kwargs; no
  soft incomplete list from those names).
- Change `get_members_if_available()` to `-> MemberListReport | None` so a
  **known-incomplete** listing surfaces its prefix + `error` (a floor count for
  progress/size) instead of collapsing to `None`; `None` now means only
  "nothing materialized and a scan would be required," not "cheap index absent
  **or** damaged." Still a peek — never scans/consumes.
- **Align RA progressive iteration with streaming (option 7):** on a terminal
  archive-level error after a recoverable prefix, RA `__iter__` /
  `stream_members` yield the prefix then raise (same caller-visible contract as
  streaming). The complete-cache sentinel is **not** published as a successful
  materialization when `error` is set (concurrency N1).
- RA extract-prep stays **fail-closed** (no partial writes from a corrupt
  archive) unless a future salvage/extract change says otherwise.
- Record Q7 decision; cross-link from Option F / backlog. Out of scope: salvage
  resync, Q5 `verify`, soft-extract report fields (Option E).

## Capabilities

### New Capabilities

<!-- none — report type lives under archive-reading -->

### Modified Capabilities

- `archive-reading` — `MemberListReport` + `members_report()`; complete-or-raise
  vs report dual; RA yield-then-raise on terminal archive errors; incomplete
  materialization must not publish as complete
- `access-mode-and-cost` — streaming vs RA listing matrix rows for the report
  accessor and RA iter failure mode
- `error-handling` — terminal archive errors may carry recovered members only
  via the report / yield-then-raise path (not a silent diagnostic-only path)
- `documentation` — usage/gotchas/api: when to use `members()` vs `members_report()`;
  Q7 / VISION (3) recipe
- `cli` — `list` (and optionally `test`) consume the report so prefix + nonzero
  exit are visible at the shell

## Impact

- Modules: `reader.py` ABC, `base_reader.py` materialization / progressive pass,
  `diagnostics.py` (or types) for `MemberListReport`, TAR EOF path as the first
  consumer, CLI `list_cmd`.
- Public API: additive `MemberListReport` + `members_report()`; **signature**
  change for `get_members_if_available()` (`list[ArchiveMember] | None` →
  `MemberListReport | None`; `len`/iterate/index callers unaffected via the
  report's sequence ergonomics); **behavioral** change for RA `__iter__` /
  `stream_members` on terminal archive errors (yield-then-raise instead of
  fail-closed before any yield). `members()` / `scan_members()` semantics
  unchanged (still raise, no incomplete return).
- Extras/deps: none.
- Tests: TAR rejected-header / strict-absent fixtures for report + RA
  yield-then-raise; cache-not-published assertions; CLI list exit + printed
  prefix.
