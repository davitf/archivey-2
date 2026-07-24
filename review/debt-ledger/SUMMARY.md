# Debt ledger — SUMMARY (pre-`0.2.0`)

> Commissioned 2026-07-20 (backlog Topics 4+5) against `main` @ `7bb862b`.
> **Refreshed 2026-07-24** against `main` @ `bdf5ffd` (post-#191 / #184 / #171).
> Analysis-only refresh: no product code changed in this pass.
> Theme files: [`structural.md`](structural.md),
> [`drift-and-decisions.md`](drift-and-decisions.md), [`tests.md`](tests.md),
> [`QUESTIONS.md`](QUESTIONS.md).

## Headline

Since the ledger landed (#169), the structural half and the falsified perf
claim are paid: **S2+S3** + **T1** in `unify-pass-driver` (#184), nightly
**wall-ratio drift** (#171), and **D1/Q2** aspirational peer bands + measured
table (#191). What still freezes at release is mostly **release honesty
artifacts** — no `SECURITY.md` (**D2**), no `CHANGELOG` (**D3**; open PR
#176), and the under-characterized rapidgzip ISIZE backstop (**DD4**; open PR
#177). Remaining generative holes (**T2/T3/T7**), stale `open-issues.md`
(**D4**), and two completed-but-unarchived OpenSpec changes (**D7**) round
out the pay list.

**Freeze-rank legend** — F3: frozen at release (public claim / API / published
docs; changing later is breaking or reputationally expensive). F2: compounds
(each release/backend/review multiplies cost). F1: internal, cost roughly
stable over time.

## The ledger, ranked by freezes-at-release cost

| # | Item | Where | Freeze | Verdict |
|---|------|-------|--------|---------|
| **D1** | VISION ≤1.3× open/list claim vs measurements / Q1 bands | `VISION.md`; `docs/costs.md` | **F3** | **DONE** (#191) — aspirational peer bands + nightly measured table (Q2 (b)); L5 → `IDEAS.md` |
| **D2** | No `SECURITY.md` / disclosure process | threat-model O5; PLAN | **F3** | **PAY** (SECURITY.md); OSS-Fuzz may trail |
| **DD1/DD3** | Wall enforcement + ZIP listing above band | `review/performance/` | **F3** | **DONE** — DD1 #171; DD3/Q2 (b) #191 |
| **D3** | No `CHANGELOG`; `0.2.0.dev0` | `pyproject.toml` | **F3** | **PAY** — open PR **#176** (rebase); form → Q5 lean = committed file |
| **DD6** | Salvage mode absent (founding use case) | PLAN / IDEAS / `--salvage` | **F3**→ok | **KEEP** — sequencing recorded; docs honest |
| **DD4** | rapidgzip ISIZE backstop under-characterized (1/13) | `openspec/changes/rapidgzip-truncation-investigation/` | **F2** | **PAY before 0.2.0** — open PR **#177** (rebase). Stdlib gzip path paid in #183 |
| **T2** | Seek-interleaving property test only for XZ | `test_seekable_streams.py` | **F2** | **PAY** — parametrize over lzip/`.Z` |
| **T3** | Benchmark gate missing RAR / encrypted / accelerator data cases | `test_benchmark_gate.py` | **F2** | **PAY** (perf P6 remainder) |
| **D4** | `open-issues.md` bucket/ref drift (P1 still under candidates) | `docs/internal/open-issues.md` | **F2** | **PAY** — 15-min sweep |
| **D7** | Completed OpenSpec changes unarchived | `unify-pass-driver`, `gzip-zlib-truncation-recovery` | **F2** | **PAY** — archive + sync |
| **T7** | Corpus matrix thin spots (ISO only in `basic`; enc-header / multi-vol outside nets) | `sample_archives.py` | **F2** | **PAY** — half-day audit |
| **T1** | Solid-RAR mutation net | `test_mutation_fuzz.py` `_SOLID_RAR_*` | **F2** | **DONE** (#184) |
| **S2/S3** | Pass-stream driver + link finalize | `_drive_pass_streams` / `_finalize_links` | **F2** | **DONE** (#184); OpenSpec ✓ Complete → **D7** |
| **D5/D6** | stop-on-failure + cli-product archives | archives | **F2** | **DONE** (2026-07-20) |
| **T4** | Free-threaded core-only; no multithread `members_report_if_available` | CI / tests | **F2** | **KEEP scope** / **PAY one test** |
| **DD7/DD8** | CLI `--json` / `--raw` remainder | IDEAS | **F2** | **KEEP** |
| **DD9–DD12** | Threat-model / C3 / api-coherence Q5 / C4 | registers | **F1-F2** | **KEEP** |
| **T5/T6** | Fault-injection leftovers; no stateful concurrency stress | tests.md | **F1** | **KEEP** |
| **DD5** | `seekable-gzip-and-block-writing` (0/24) | in-flight | **F1** | **KEEP** — post-0.2.0 |
| **S1/S4/…** | Error boundary; ReaderState; module seams; `VerifyingStream` parked | structural.md | — | **fine** |
| **N1** | `pyppmd` teardown UAF / exit-after-green residual | `known-issues.md`; #188/#189 | **F1** | **KEEP** — mitigated; CI soft-pass until hot-race clear |

## The remaining pre-0.2.0 pay list, in order

1. **D2** — write `SECURITY.md`.
2. **D3 + Q5** — rebase/merge **#176** (`CHANGELOG.md`).
3. **DD4** — rebase/finish **#177** (`rapidgzip-truncation-investigation`).
4. **T2** — parametrize seek-interleaving over lzip/`.Z`.
5. **D4 + D7** — `open-issues.md` sweep; archive `unify-pass-driver` +
   `gzip-zlib-truncation-recovery`.
6. **T3** — benchmark-gate RAR/encrypted/accelerator data cases.
7. **T7** — corpus-matrix audit.
8. **T4 (half)** — one `members_report_if_available` multithread barrier test.

## Paid since the ledger was commissioned

| Item | Landed |
|------|--------|
| **Q1 / DD1** nightly wall-ratio drift | #171 |
| **D5/D6** archive stop-on-failure + cli-product | #170 |
| **Q3 / S2+S3 / T1** unify pass driver + solid-RAR mutation | #184 |
| **D1 / Q2 / DD3** aspirational bands + measured table | #191 |
| Stdlib gzip recoverable truncation (adjacent to DD4) | #183 |
| ADR 0014 — integrity from reads, never `close()` | #186 |
| `pyppmd` quiesce-on-close + valgrind UAF gate | #188/#189 |

## What is actually fine (don't re-review)

- **S1** held; **S2/S3** paid (`_drive_pass_streams` + `_finalize_links`).
- **S4/ReaderState** rebuilt around owner-carrying tokens.
- Module splits earning seams; public export tiering deliberate.
- Docs↔spec↔code sync works through OpenSpec; VISION drift closed by #191.
- Fuzz architecture coherent; T1 widened solid-RAR intake; T2/T7 remain.
- `src/` has essentially zero comment-level debt.
