# Debt ledger — SUMMARY (pre-`0.2.0`)

> Commissioned 2026-07-20 (backlog Topics 4+5) against `main` @ `7bb862b`.
> Analysis-only: no code changed; `openspec validate --all` green (27/27).
> Theme files: [`structural.md`](structural.md),
> [`drift-and-decisions.md`](drift-and-decisions.md), [`tests.md`](tests.md),
> [`QUESTIONS.md`](QUESTIONS.md).

## Headline

The tree is in unusually honest shape for a pre-release codebase: **one**
deferred-work marker in all of `src/`, specs strict-clean, S1 (the error
boundary) paid and holding, S4 (ReaderState) reworked rather than accreted,
CLI docs current, and the threat-model register genuinely authoritative. The
debt that matters clusters in three places: (1) **a published performance
claim the project's own measurements falsify** (`VISION.md` ≤1.3× vs the
maintainer's already-recorded re-scoping that never reached the docs) plus two
missing release artifacts (SECURITY.md, CHANGELOG); (2) the **S2/S3
structural prediction came true** — the pass-driver skeleton now exists in
four divergent copies (RAR's is the fourth, exactly as forecast in 2026-07-12)
and the member-list pipeline still carries mirrored double-fault guards — but
none of it is public surface, so the recommended verdict is a *hard entry gate
on the next backend*, not a release blocker; (3) the **generative test nets
stop at the declarative corpus** — solid-RAR demux, the code where the RAR
review found its bugs, is never mutated or swept, and the seek-interleaving
property test that closed stream-decoder F5 exists only for XZ.

**Freeze-rank legend** — F3: frozen at release (public claim / API / published
docs; changing later is breaking or reputationally expensive). F2: compounds
(each release/backend/review multiplies cost). F1: internal, cost roughly
stable over time.

## The ledger, ranked by freezes-at-release cost

| # | Item | Where | Freeze | Verdict |
|---|------|-------|--------|---------|
| **D1** | VISION/philosophy/costs still publish **≤1.3× for open/list** — falsified by own measurements; maintainer's Q1 re-scoping (peer-ratio bands) never reached the docs | `VISION.md:74-76`; `review/performance/QUESTIONS.md` Q1 | **F3** | **PAY** — re-word before release; band honesty doesn't wait on Q2 |
| **D2** | No `SECURITY.md` / disclosure process — gates the "safe" positioning per threat-model O5.4 + PLAN | `docs/internal/threat-model.md` O5 | **F3** | **PAY** (SECURITY.md); OSS-Fuzz may trail |
| **DD1/DD3** | Perf **Q2** (wall enforcement) + ZIP listing above its own band (L5 or honest number) | `review/performance/` | **F3** | **DECIDE** — `QUESTIONS.md` Q1/Q2 |
| **D3** | No `CHANGELOG`; `0.2.0.dev0` sets the record at release | `pyproject.toml:7` | **F3** | **PAY** (cheap; form → Q5) |
| **DD6** | Salvage mode absent though it's the founding use case | PLAN / IDEAS / reserved `--salvage` | **F3**→ok | **KEEP** — sequencing decision already recorded; docs verified honest |
| **T1** | Mutation-fuzz + conformance sweep cover only declarative `CORPUS`; **solid-RAR demux** (where RAR-review bugs lived) has no generative net | `tests/test_mutation_fuzz.py:118`; `rar_reader.py:578-649` | **F2** | **PAY** — mutate static fixtures / build solid RAR into corpus |
| **T3** | Benchmark gate has no RAR/encrypted/accelerator data cases — D1's claim can't be honest for unmeasured paths | `tests/test_benchmark_gate.py` | **F2** | **PAY** (already tracked as perf P6 remainder) |
| **T2** | Seek-interleaving property test exists only for XZ; lzip/`.Z` share the arithmetic class that hid F1 | `test_seekable_streams.py:507` | **F2** | **PAY** — parametrize existing test |
| **S3** | Pass driver now **4 divergent copies** (base/TAR/7z/RAR; close-previous enforced 3 ways + not at all in one) — S3's prediction realized; next backend makes 5 | `base_reader.py:450`, `tar_reader.py:439`, `sevenzip_reader.py:303`, `rar_reader.py:578` | **F2** | **KEEP thru 0.2.0, PAY as entry gate for next backend** — Q3 to confirm |
| **S2** | Member-list pipeline half-unified: shared stamper/publication landed, but dual drive loops + **mirrored double-fault guards** remain | `base_reader.py:782-816` vs `:1021-1048,1640-1690` | **F2** | **KEEP thru 0.2.0, PAY with S3** (one change) |
| **D4** | `open-issues.md` contradicts its own bucket rules (P1 decided+implemented yet listed as to-fix; dead change-path refs) | `docs/internal/open-issues.md:34-55,185` | **F2** | **PAY** — 15-min sweep |
| **D5/D6** | Lifecycle housekeeping: `stop-on-failure-not-policy` complete-but-unarchived; `cli-product/` review done-pending-archive | `openspec list`; `review/STATUS.md` | **F2** | **DONE** (2026-07-20) — OpenSpec → `archive/2026-07-20-stop-on-failure-not-policy/`; review → `archive/2026-07-20-cli-product/` |
| **T7** | Corpus matrix thin spots post-oracle-retirement: ISO only in `basic`; encrypted-header 7z / multi-volume outside the nets | `tests/sample_archives.py:307-345` | **F2** | **PAY** — half-day audit + cheap extensions |
| **DD4** | rapidgzip ISIZE truncation backstop ships self-describedly under-characterized (change at 1/11) | `openspec/changes/rapidgzip-truncation-investigation/` | **F2** | **PAY before 0.2.0** (Q4 decided 2026-07-20); implement later — see change `design.md` |
| **T4** | Free-threaded CI core-only; no multithread `members_report_if_available` test | `ci.yml:168-191` | **F2** | **KEEP scope** (already published honestly) / **PAY one test** |
| **DD7/DD8** | CLI `--json` (wait for schema) and `--raw` quoting remainder | cli-product Q2/Q4 | **F2** | **KEEP** — decided/additive; space reserved |
| **DD9–DD12** | Threat-model residuals (O1/O6/O7/O8), C3 fidelity, api-coherence Q5, C4 scope | registers | **F1-F2** | **KEEP** — all additive, all already recorded with rationale |
| **T5/T6** | Remaining fault injections (symlink-EPERM, raw ENOSPC); no stateful concurrency stress | tests.md | **F1** | **KEEP** recorded; pay opportunistically |
| **DD5** | `seekable-gzip-and-block-writing` (0/24, spec-only, Phase 8) | in-flight change | **F1** | **KEEP** — post-0.2.0 by plan |
| **S1/S4/S5/S6** | Error boundary paid & holding; ReaderState reworked cleanly; module splits earning seams; one in-code marker; `VerifyingStream` leftover parked | structural.md | — | **fine** (S1 residue + VerifyingStream: KEEP) |

## The pre-0.2.0 pay list, in order

1. **D1** — re-word the perf claim (after deciding Q1/Q2 in `QUESTIONS.md`).
2. **D2** — write `SECURITY.md`.
3. **D3** — start `CHANGELOG.md`.
4. **T1 + T2** — widen the generative nets (solid-RAR mutation; lzip/`.Z`
   seek property test). Cheap, reuses machinery, and T1 doubles as the
   safety net for the eventual S2/S3 change.
5. **T3** — benchmark-gate RAR/encrypted/accelerator cases (D1 dependency).
6. **D4 + T7** — the housekeeping sweep (open-issues, corpus-matrix audit).
   (**D5/D6** archived 2026-07-20.)
7. Record the **S2/S3 entry-gate** decision (Q3) in `PLAN.md`/`IDEAS.md`.
8. **DD4 / rapidgzip-truncation-investigation** — characterize + narrow/extend/remove
   ISIZE backstop before 0.2.0 (Q4 = PAY; implementation deferred, change enriched).

Nothing else on the ledger should block 0.2.0; every KEEP above has its
justification written down here or in the register it points to, which is
what the zero-debt framing requires.

## What is actually fine (don't re-review)

- **S1** held under two new backends — RAR/7z route raw errors through the
  shared boundary; remaining direct stamps are origination sites, not
  translation drift.
- **S4/ReaderState** was rebuilt around owner-carrying tokens, not patched
  into a sixth mechanism.
- **Module splits** (`config`/`internal.config`, `measurement`,
  `extraction_types`, the 7z quartet, `timestamps`) each document a
  load-bearing reason; no gratuitous seams. Public export tiering in
  `__init__.py` is deliberate and annotated.
- **Docs↔spec↔code sync discipline is working** where changes flowed through
  OpenSpec: exit codes, STOP-failures-only, ExtractionStatus renames, TAR EOF
  Option F all agree across spec/docs/code. The drift found is confined to
  registers updated by hand (`open-issues.md`) and the one pre-OpenSpec
  document (VISION).
- **The fuzz architecture** (mutation + Hypothesis + Atheris, accelerators
  off with the rationale recorded) — coherent; the ledger widens its intake,
  not its design.
- `src/` carries essentially **zero comment-level debt** — the registers are
  doing their job.
