## Why

The 2026-07-12 deep-simplification review predicted that native RAR would add a
fourth divergent copy of the `stream_members` close-previous / open-current /
yield / cleanup skeleton. It did (`review/debt-ledger/structural.md` S3). The
member-list pipeline is only half-unified (S2): shared stamper and publication
landed, but two drive loops and two mirrored double-fault guards remain. Debt-ledger
**Q3** decided **pay before 0.2.0** (not “entry gate for the next backend”): the
maintainer prefers clean structure over shipping with known copy-#5 debt, and the
existing suite should catch regressions. The solid-RAR demux path that carries the
trickiest invariants is currently example-tested only — mutation fuzz never hits it
(T1).

## What Changes

- **T1 first:** extend mutation fuzz to curated static solid RAR fixtures
  (`basic_solid__.rar`, `basic_solid__rar4.rar`) so damaged solid demux is under
  the generative net before the refactor.
- **S3:** one shared pass-stream driver on `BaseArchiveReader`; backends supply
  open/resource hooks instead of re-copying the close-previous loop. TAR keeps
  “no previous-close” (tarfile owns invalidation) as an explicit hook flag, not an
  undocumented omission.
- **S2:** one link-finalizer + one double-fault policy used by both eager
  materialization and progressive pass finalize; eliminate mirrored guard prose.
- No public API renames or behavior changes intended. **Not BREAKING** if the
  must-not-break suite stays green.

## Capabilities

### New Capabilities

<!-- none -->

### Modified Capabilities

- `testing-contract` — mutation harness MUST cover solid RAR demux (T1); S2/S3
  themselves are internal and do not change public reading requirements.

## Impact

- **Modules:** `internal/base_reader.py` (driver + finalize); thin rewrites of
  `_iter_with_data` in `tar_reader.py`, `sevenzip_reader.py`, `rar_reader.py`.
- **Public API:** none intended.
- **Tests:** T1 mutation params; existing solid RAR/7z, progressive TAR,
  double-fault, `stream_members` close/ownership suites are the gate.
- **Docs:** record Q3=(b) in `review/debt-ledger/`; do **not** add an “entry gate”
  to `PLAN.md` / `IDEAS.md` (that was the rejected (a) framing).
