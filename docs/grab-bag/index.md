# Grab-bag (triage later)

Material that does **not** belong in the end-user guide or the curated decision log, but
must not be deleted. Sort these into OpenSpec annexes, decisions, or `docs/internal/`
when someone has time.

| Doc | Likely status | Notes |
| --- | --- | --- |
| [SPEC.md](SPEC.md) | **Superseded as authority** by `openspec/specs/` | Large prose contract; useful archaeology; may drift |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Partially superseded | Module layout + trade-offs; load-bearing “why” extracted to `docs/decisions/` |
| [COMPARISON.md](COMPARISON.md) | Historical | DEV vs clean-slate comparison; Intent-enum recommendation later reversed |
| [ASYNC.md](ASYNC.md) | Exploration | Not a v1 decision; sync-only stands; seams still interesting |
| [parallel-reader.md](parallel-reader.md) | Exploration → mostly landed | Concurrent-member-streams superseded much of this; keep for audit notes / benchmarks pointers |

## Suggested triage (not done in this pass)

1. Diff `SPEC.md` / `ARCHITECTURE.md` against `openspec/specs/` — file OpenSpec follow-ups
   for any unique requirements still only in prose.
2. Fold remaining ARCHITECTURE trade-offs into new decision records or delete duplicates.
3. Archive or slim `COMPARISON.md` once no open questions remain.
4. Promote any accepted ASYNC seams into a real OpenSpec change; otherwise leave here.
5. Shrink `parallel-reader.md` to a short “historical audit” or move lock-order tables
   next to `reader-concurrency` if still useful.

Root docs that stay put: `VISION.md`, `PLAN.md`, `IDEAS.md`, `CONTRIBUTING.md`,
`CLAUDE.md`, `AGENTS.md`. Thin redirect stubs remain at the old root paths for
`SPEC.md` / `ARCHITECTURE.md` / `COMPARISON.md` / `ASYNC.md`.
