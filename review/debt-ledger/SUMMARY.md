# Debt ledger — SUMMARY (pre-`0.2.0`)

> Commissioned 2026-07-20 (backlog Topics 4+5) against `main` @ `7bb862b`.
> **Refreshed 2026-07-23** against `main` @ `8cc3ea5` (post-#184/#183/#171/#188/#189).
> Analysis-only refresh: no product code changed in this pass.
> Theme files: [`structural.md`](structural.md),
> [`drift-and-decisions.md`](drift-and-decisions.md), [`tests.md`](tests.md),
> [`QUESTIONS.md`](QUESTIONS.md).

## Headline

Since the ledger landed (#169), the structural half of the pay list has been
paid: **S2+S3** and their **T1** safety net shipped in `unify-pass-driver`
(#184), and the nightly **wall-ratio drift** gate closed debt-ledger Q1 /
perf Q2 (#171). What still freezes at release is almost all **release
honesty artifacts** — VISION still publishes the falsified ≤1.3× open/list
claim (**D1**; open PR #172), no `SECURITY.md` (**D2**), no `CHANGELOG`
(**D3**; open PR #176, conflicts), and the under-characterized rapidgzip
ISIZE backstop (**DD4**; open PR #177, conflicts). The remaining generative
test holes (**T2/T3/T7**) and the stale `open-issues.md` bucket (**D4**) are
still open; two completed OpenSpec changes (`unify-pass-driver`,
`gzip-zlib-truncation-recovery`) need archiving (**D7**).

**Freeze-rank legend** — F3: frozen at release (public claim / API / published
docs; changing later is breaking or reputationally expensive). F2: compounds
(each release/backend/review multiplies cost). F1: internal, cost roughly
stable over time.

## The ledger, ranked by freezes-at-release cost

| # | Item | Where | Freeze | Verdict |
|---|------|-------|--------|---------|
| **D1** | VISION still publishes **≤1.3× for open/list** — falsified by own measurements; Q1 peer-ratio bands never reached the docs | `VISION.md:76`; perf Q1 | **F3** | **PAY** — open PR **#172** (aspirational bands + measured ratios); merge/refresh |
| **D2** | No `SECURITY.md` / disclosure process — gates the "safe" positioning per threat-model O5.4 + PLAN | `docs/internal/threat-model.md` O5 | **F3** | **PAY** (SECURITY.md); OSS-Fuzz may trail |
| **DD1/DD3** | Perf wall enforcement **DECIDED**; ZIP listing above its own band (L5 or honest number) | `review/performance/` | **F3** | **DD1 DONE** (#171). **DD3 DECIDE** with D1 — #172 embodies lean (b) publish honest/aspirational |
| **D3** | No `CHANGELOG`; `0.2.0.dev0` sets the record at release | `pyproject.toml` | **F3** | **PAY** — open PR **#176** (conflicts; rebase); form → Q5 lean = committed file |
| **DD6** | Salvage mode absent though it's the founding use case | PLAN / IDEAS / reserved `--salvage` | **F3**→ok | **KEEP** — sequencing decision already recorded; docs verified honest |
| **DD4** | rapidgzip ISIZE truncation backstop still under-characterized (change **1/13**) | `openspec/changes/rapidgzip-truncation-investigation/` | **F2** | **PAY before 0.2.0** — open PR **#177** (conflicts; rebase). Stdlib gzip path separately paid in #183 |
| **T2** | Seek-interleaving property test exists only for XZ; lzip/`.Z` share the arithmetic class that hid F1 | `test_seekable_streams.py` | **F2** | **PAY** — parametrize existing test |
| **T3** | Benchmark gate has no RAR/encrypted/accelerator data cases — D1's claim can't be honest for unmeasured paths | `tests/test_benchmark_gate.py` | **F2** | **PAY** (already tracked as perf P6 remainder) |
| **D4** | `open-issues.md` contradicts its own bucket rules (P1 decided+implemented yet listed as to-fix; dead change-path refs) | `docs/internal/open-issues.md:34-55,191` | **F2** | **PAY** — 15-min sweep |
| **D7** | Completed OpenSpec changes unarchived: `unify-pass-driver`, `gzip-zlib-truncation-recovery` | `openspec list` | **F2** | **PAY** — archive + sync (same lifecycle as D5) |
| **T7** | Corpus matrix thin spots: ISO only in `basic`; encrypted-header 7z / multi-volume outside the nets | `tests/sample_archives.py` | **F2** | **PAY** — half-day audit + cheap extensions |
| **T1** | Solid-RAR mutation net | `tests/test_mutation_fuzz.py` `_SOLID_RAR_*` | **F2** | **DONE** (#184) — static solid RAR4/RAR5 under mutation |
| **S2/S3** | Pass-stream driver + link finalize unification | `BaseArchiveReader._drive_pass_streams` / `_finalize_links` | **F2** | **DONE** (#184) — OpenSpec `unify-pass-driver` ✓ Complete (needs D7 archive) |
| **D5/D6** | Lifecycle housekeeping: `stop-on-failure-not-policy`; `cli-product/` | archives | **F2** | **DONE** (2026-07-20) |
| **T4** | Free-threaded CI core-only; no multithread `members_report_if_available` test | `ci.yml`; `test_concurrent_multithread.py` | **F2** | **KEEP scope** / **PAY one test** (still missing) |
| **DD7/DD8** | CLI `--json` (wait for schema) and `--raw` quoting remainder | cli-product Q2/Q4 | **F2** | **KEEP** — decided/additive; space reserved |
| **DD9–DD12** | Threat-model residuals (O1/O6/O7/O8), C3 fidelity, api-coherence Q5, C4 scope | registers | **F1-F2** | **KEEP** — all additive, all already recorded with rationale |
| **T5/T6** | Remaining fault injections (symlink-EPERM, raw ENOSPC); no stateful concurrency stress | tests.md | **F1** | **KEEP** recorded; pay opportunistically |
| **DD5** | `seekable-gzip-and-block-writing` (0/24, spec-only, Phase 8) | in-flight change | **F1** | **KEEP** — post-0.2.0 by plan |
| **S1/S4/…** | Error boundary paid & holding; ReaderState reworked cleanly; module splits earning seams; `VerifyingStream` leftover parked | structural.md | — | **fine** |
| **N1** | `pyppmd` teardown UAF / exit-after-green (mitigated; CI soft-pass residual) | `known-issues.md`; #188/#189 | **F1** | **KEEP** — defense-in-depth + valgrind gate landed; drop `--allow-exit-after-green` only after hot-race platforms clear |

## The remaining pre-0.2.0 pay list, in order

1. **D1 + DD3** — land/refresh **#172** (re-word VISION/philosophy/costs; decide
   aspirational bands vs L5 — #172 = lean (b)).
2. **D2** — write `SECURITY.md`.
3. **D3 + Q5** — rebase/merge **#176** (`CHANGELOG.md`).
4. **DD4** — rebase/finish **#177** (`rapidgzip-truncation-investigation`).
5. **T2** — parametrize seek-interleaving over lzip/`.Z`.
6. **D4 + D7** — `open-issues.md` sweep; archive `unify-pass-driver` +
   `gzip-zlib-truncation-recovery`.
7. **T3** — benchmark-gate RAR/encrypted/accelerator data cases (D1 honesty).
8. **T7** — corpus-matrix audit (ISO beyond `basic`; note deliberate exclusions).
9. **T4 (half)** — one `members_report_if_available` multithread barrier test.

Nothing else on the ledger should block 0.2.0; every KEEP above has its
justification written down here or in the register it points to.

## Paid since the ledger was commissioned (do not re-open)

| Item | Landed |
|------|--------|
| **Q1 / DD1** nightly wall-ratio drift gate | #171 |
| **D5/D6** archive stop-on-failure + cli-product | #170 |
| **Q3 / S2+S3 / T1** unify pass driver + solid-RAR mutation | #184 |
| Stdlib gzip recoverable truncation (adjacent to DD4; not a substitute) | #183 |
| ADR 0014 — integrity verdicts from reads, never `close()` | #186 |
| `pyppmd` quiesce-on-close + valgrind UAF gate (mitigation, not closure) | #188/#189 |

## What is actually fine (don't re-review)

- **S1** held under two new backends — RAR/7z route raw errors through the
  shared boundary; remaining direct stamps are origination sites, not
  translation drift.
- **S2/S3** paid — one `_drive_pass_streams` + one `_finalize_links` double-fault
  policy; backends supply hooks (`close_previous`, solid resource cleanup).
- **S4/ReaderState** was rebuilt around owner-carrying tokens, not patched
  into a sixth mechanism.
- **Module splits** each document a load-bearing reason; public export tiering
  in `__init__.py` is deliberate and annotated.
- **Docs↔spec↔code sync discipline is working** where changes flowed through
  OpenSpec. Remaining drift is hand-updated registers (`open-issues.md`) and
  the one pre-OpenSpec document (VISION) — still D1/D4.
- **The fuzz architecture** is coherent; T1 widened intake for solid RAR;
  T2/T7 remain the intake widenings left.
- `src/` carries essentially **zero comment-level debt** — the registers are
  doing their job.
