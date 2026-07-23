# Doc ↔ spec ↔ code drift + the deferred-decision register

Original refs: `main` @ `7bb862b`. **Status refresh 2026-07-23** against
`main` @ `8cc3ea5`. Mechanical baseline at refresh: `openspec list` shows
`unify-pass-driver` ✓ Complete, `gzip-zlib-truncation-recovery` ✓ Complete,
`rapidgzip-truncation-investigation` 1/13, `seekable-gzip-and-block-writing`
0/24. (`openspec validate --all` not re-run for this docs-only refresh.)

## D1 — the VISION ≤1.3× performance claim still doesn't match measurements or the Q1 re-scoping (PAY — top of the ledger)

`VISION.md:76` still states the budget as "**≤ 1.3×** stdlib wall-time for the
common paths (open/list/read/extract…)". The performance review's measurements
and the maintainer's Q1 direction (peer-ratio bands for metadata ops;
decompression-dominated ≤1.3×) have **not** reached `VISION.md`,
`docs/philosophy.md`, or `docs/costs.md`. Nightly wall-*drift* enforcement
landed (#171 / DD1) and unblocks the *enforcement* sentence; the *band*
honesty still does not.

**PAY before 0.2.0.** Open PR **#172** ("Mark perf peer bands aspirational;
publish measured ratios") embodies the lean for debt-ledger Q2 / DD3 — ship
with aspirational bands + published measured numbers; L5 as named follow-up.
CI checks were green on that PR at refresh time; merge or refresh as needed.

## D2 — no SECURITY.md / disclosure process (PAY)

Still absent @ `8cc3ea5`. Threat-model O5.4 and PLAN's release bundle both
require it before any public "safe" claim. **PAY (SECURITY.md) before 0.2.0;
OSS-Fuzz may follow.** No open PR at refresh time.

## D3 — no CHANGELOG (PAY — cheap)

Still absent on `main`. Open PR **#176** adds `CHANGELOG.md` + every-release
checklist (Q5 lean = committed file) but was **CONFLICTING** at refresh —
rebase then merge. **PAY before 0.2.0.**

## D4 — `docs/internal/open-issues.md` still stale against its own resolutions (PAY)

Still true @ `8cc3ea5`:

- **P1** (TAR EOF Option F) is "DECIDED + IMPLEMENTED" yet sits under
  "**Product — candidates to fix**"; refs point at
  `openspec/changes/decide-strict-archive-eof-default/` (archived — live path
  gone); "Suggested first cuts" item 1 still says "apply it".
- **P6** / unrar-piping pointer: open PR #101 may still be the home; verify
  whether the investigation doc landed or the ref is dead.

**PAY** — 15-minute sweep (move P1 to Closed, fix archive refs, drop/update
suggested-first-cuts).

## D5 — `stop-on-failure-not-policy` archived — **DONE (2026-07-20)**

## D6 — `cli-product/` archived — **DONE (2026-07-20)**

## D7 — completed OpenSpec changes unarchived (PAY — new since ledger)

Same lifecycle debt D5 was:

| Change | Status @ refresh | Action |
|--------|------------------|--------|
| `unify-pass-driver` | ✓ Complete (#184) | Archive + sync main specs if needed |
| `gzip-zlib-truncation-recovery` | ✓ Complete (#183) | Archive + sync |

`rapidgzip-truncation-investigation` stays live until DD4 finishes.

## What is *not* drifting (checked, fine)

- CLI docs / ExtractionStatus / `OnError.STOP` failures-only / threat-model
  spot checks from the original pass still hold; #183/#184/#186 flowed through
  OpenSpec/ADR and kept prose aligned where they touched docs.
- Specs remain the authority for shipped capabilities; `archive-writing` is
  still the labeled unbuilt Phase 9 capability.
- `docs/grab-bag/` stays historical/non-normative — KEEP past 0.2.0.

## Adjacent landings (not ledger items, but change the backdrop)

- **#183** `gzip-zlib-truncation-recovery` — stdlib gzip on zlib
  `DecompressorStream` with recoverable truncation; ADR **#186** (0014)
  locks integrity verdicts on reads, never `close()`. Does **not** close DD4
  (accelerator ISIZE backstop is a separate path).
- **#188/#189** `pyppmd` mitigations + valgrind UAF gate — see ledger **N1**
  / `known-issues.md`; residual CI soft-pass is recorded, not release-blocking
  under the current honest scoping.

## The deferred-decision register

| ID | Decision | Where it lives | Verdict |
|---|---|---|---|
| **DD1** | Performance **Q2** — wall-budget enforcement | `review/performance/QUESTIONS.md` Q2 | **DONE (2026-07-20)** — #171 nightly drift |
| DD2 | Performance **Q4** — verify-skip knob | same, Q4 | **KEEP** (lean leave-as-is; perf case ~nil post-#137). Close when archiving `performance/` |
| DD3 | ZIP listing above its own band — **L5** or publish the honest number | STATUS residual; debt-ledger Q2 | **DECIDE with D1** — open #172 = lean (b) aspirational + measured |
| DD4 | `rapidgzip-truncation-investigation` (1/13) | in-flight change; open **#177** (conflicts) | **PAY before 0.2.0** — rebase/finish |
| DD5 | `seekable-gzip-and-block-writing` (0/24, Phase 8) | in-flight change | **KEEP** — post-0.2.0 by plan |
| DD6 | Salvage / best-effort read mode | `backlog.md`, `IDEAS.md` | **KEEP for 0.2.0** — recorded sequencing |
| DD7 | CLI **P4** `--json` | cli-product / IDEAS | **KEEP** — wait for hash/member schema |
| DD8 | CLI **Q4** `--raw` / TTY-only quoting | debt-ledger | **KEEP** — additive |
| DD9 | Threat-model O1/O6/O7/O8 residuals | `threat-model.md` | **KEEP** — additive, registered |
| DD10 | C3 metadata fidelity (xattrs/ACLs) | threat-model / IDEAS | **KEEP** — binds at writing-spec time |
| DD11 | api-coherence **Q5** `verify`/`VerifyReport` | `IDEAS.md` | **KEEP** — past 0.2.0 |
| DD12 | Free-threaded claim scope (core-only CI) | threat-model C4 | **KEEP** — already published honestly |
| **N1** | `pyppmd` teardown UAF / exit-after-green residual | `known-issues.md`; #188/#189 | **KEEP** — mitigated; soft-pass until hot-race clear |
