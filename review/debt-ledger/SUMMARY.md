# Debt ledger — SUMMARY (pre-`0.2.0`)

> Commissioned 2026-07-20 (backlog Topics 4+5) against `main` @ `7bb862b`.
> Analysis-only: no code changed; `openspec validate --all` green (27/27).
> Theme files: [`structural.md`](structural.md),
> [`drift-and-decisions.md`](drift-and-decisions.md), [`tests.md`](tests.md),
> [`QUESTIONS.md`](QUESTIONS.md).
>
> **Refreshed 2026-07-24** against `main` @ `8cc3ea5` (14 commits since the
> baseline). See [Update — 2026-07-24](#update--2026-07-24) below; the original
> ledger body is unchanged (line references still cite `7bb862b`).

## Update — 2026-07-24

Reviewed every commit merged after the ledger's baseline (`7bb862b`, #168) through
`main` @ `8cc3ea5` (#189). Three of the pay-list items are now **paid**, one
decision moved from *decided* to *implemented*, and three **new** items appeared
that the original ledger could not have seen.

**Paid since the ledger** (verified in-tree):

- **S2 + S3 → DONE (#184, `unify-pass-driver` ✓ Complete).** The four hand-rolled
  close-previous loops collapsed into one shared `BaseArchiveReader._drive_pass_streams`
  (`base_reader.py:450`, called from base/TAR/7z/RAR); the mirrored double-fault
  guards and dual link finalizers collapsed into a single `_finalize_pass_links`
  (`base_reader.py:1100`). The predicted "fifth copy on the next backend" cost is
  gone. **Q3=(b) delivered.**
- **T1 → DONE (#184).** `test_mutation_fuzz.py` now mutates the static **solid**
  RAR4/RAR5 fixtures (`_SOLID_RAR_SOURCES`, `test_mutation_fuzz.py:131-145`) — the
  solid-demux path where RAR-review bugs lived is inside a generative net, and it
  doubled as the safety net for the S2/S3 refactor above.
- **DD1 / Q1 → IMPLEMENTED (#171).** The nightly wall-ratio *drift* gate decided in
  Q1(a) now exists: `benchmark-wall.yml` runs `--wall-drift-baseline` against the
  previous successful JSON. This is the enforcement wording D1 was waiting on —
  D1 can now say "structural axes gated per-PR, wall-ratio drift gated nightly."

**New items (not on the original ledger):**

- **N1 — pyppmd native use-after-free (PPMd, `[7z]` extra) (#188, #189).** A real
  memory-safety defect in `pyppmd` 1.3.x: an output-buffer UAF (`ThreadDecoder.c:134`,
  freed by `OutputBuffer_Finish`) when a decode worker is left parked on a
  truncated/over-budgeted member and later resumed at teardown. Not adversarial-only —
  the same overshoot family fired on valid streams. **Mitigated in archivey**
  (bounded decode, **required `pack_size` for PPMd7** incl. the encrypted-folder
  plumbing, quiesce-on-close `_quiesce_worker` on close/seek/GC, capped NUL flush,
  and a deterministic `scripts/ppmd_uaf_valgrind.py` gate). **Residual:** the upstream
  bug is unfixed (report ready to file — results doc §J), and CI still carries
  `--allow-exit-after-green` for the teardown-`Ppmd7T_Free` race. **Freeze: F2** —
  optional path, but a memory-safety story a "safe" release must be honest about.
  **Verdict: KEEP for 0.2.0 with the residual recorded** (mitigations shipped,
  path is opt-in `[7z]`); **file the upstream report** and revisit dropping
  `--allow-exit-after-green` once the valgrind gate runs on the hot-race platforms.
- **N2 — gzip-family decode unified on `DecompressorStream` (#180/#182/#183,
  `gzip-zlib-truncation-recovery` ✓ Complete) + ADR 0014 (#186).** Stdlib gzip now
  decodes through the zlib gzip-window path with recoverable-truncation semantics
  (prefix delivered, error on the next empty read; `readall` still raises), and
  **ADR 0014** makes the close contract standing law: content/decode verdicts
  surface from `read`/size/seek, **never from `close()`**. This retires a class of
  the byte-at-a-time truncation bugs and tightens the `VerifyingStream`/`MemberVerifier`
  fused path. **No open debt — recorded here as landed context** (touches the same
  truncation surface as DD4).
- **N3 — two ✓ Complete changes are unarchived** (`unify-pass-driver`,
  `gzip-zlib-truncation-recovery`). Same lifecycle-housekeeping shape as the old
  D5/D6. **PAY** — cheap `openspec archive` sweep; sync main specs first.

**Still open, unchanged** (the real pre-0.2.0 remainder): **D1** (VISION/philosophy/
costs still publish ≤1.3× — `VISION.md:76` unchanged), **D2** (no `SECURITY.md`),
**D3** (no `CHANGELOG`), **T2** (seek-interleaving still XZ-only), **T3** (benchmark
gate still has no RAR/encrypted/accelerator data cases), **D4** (`open-issues.md`
P1 still miscategorized + dead ref at `:37,:192`), **T7** (corpus-matrix ISO audit),
**DD4** (`rapidgzip-truncation-investigation`, now **1/13**, Q4=PAY, impl still deferred).

**Revised pre-0.2.0 pay list, in order:**

1. **D1** — re-word the ≤1.3× claim to the Q1 bands (enforcement wording now
   unblocked by #171).
2. **D2** — write `SECURITY.md`.
3. **D3** — start `CHANGELOG.md` (Q5: committed file).
4. **T2** — parametrize the seek-interleaving property test over lzip/`.Z`.
   *(T1 is done.)*
5. **T3** — benchmark-gate RAR/encrypted/accelerator data cases (D1 dependency).
6. **D4 + T7 + N3** — the housekeeping sweep: `open-issues.md` fix, corpus-matrix
   audit, and `openspec archive` the two complete changes.
7. **DD4 / rapidgzip-truncation-investigation** — characterize → narrow/extend/remove.
8. **N1 — file the pyppmd upstream report**; keep the mitigation + valgrind gate;
   revisit `--allow-exit-after-green` post-gate-on-hot-platforms.

---

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
none of it is public surface, so the *original* recommended verdict was a *hard
entry gate on the next backend*, not a release blocker (**overridden 2026-07-20:
Q3 = (b) pay before 0.2.0** via `unify-pass-driver`); (3) the **generative test nets
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
| **DD1/DD3** | Perf **Q2** (wall enforcement) + ZIP listing above its own band (L5 or honest number) | `review/performance/` | **F3** | **DD1 IMPLEMENTED (2026-07-24, #171)** — nightly wall-drift gate (`benchmark-wall.yml`); DD3/Q2 still DECIDE |
| **N1** | pyppmd native **use-after-free** (PPMd `[7z]` path); mitigated in-tree, upstream unfixed, CI still `--allow-exit-after-green` | `docs/internal/known-issues.md:171`; `scripts/ppmd_uaf_valgrind.py` | **F2** | **KEEP for 0.2.0** (mitigated + opt-in) — **file upstream report**; revisit CI flag post-gate |
| **N2** | gzip decode moved onto `DecompressorStream` + **ADR 0014** close contract | `gzip-zlib-truncation-recovery` ✓; `docs/decisions/` ADR 0014 | — | **DONE (2026-07-24, #183/#186)** — landed context; no open debt |
| **D3** | No `CHANGELOG`; `0.2.0.dev0` sets the record at release | `pyproject.toml:7` | **F3** | **PAY** (cheap; form → Q5) |
| **DD6** | Salvage mode absent though it's the founding use case | PLAN / IDEAS / reserved `--salvage` | **F3**→ok | **KEEP** — sequencing decision already recorded; docs verified honest |
| **T1** | Mutation-fuzz + conformance sweep cover only declarative `CORPUS`; **solid-RAR demux** (where RAR-review bugs lived) has no generative net | `tests/test_mutation_fuzz.py:118`; `rar_reader.py:578-649` | **F2** | **DONE (2026-07-24, #184)** — solid RAR4/RAR5 fixtures now mutated (`test_mutation_fuzz.py:131`) |
| **T3** | Benchmark gate has no RAR/encrypted/accelerator data cases — D1's claim can't be honest for unmeasured paths | `tests/test_benchmark_gate.py` | **F2** | **PAY** (already tracked as perf P6 remainder) |
| **T2** | Seek-interleaving property test exists only for XZ; lzip/`.Z` share the arithmetic class that hid F1 | `test_seekable_streams.py:507` | **F2** | **PAY** — parametrize existing test |
| **S3** | Pass driver was **4 divergent copies** (base/TAR/7z/RAR; close-previous enforced 3 ways + not at all in one) — S3's prediction realized | `base_reader.py:450`, `backends/tar_reader.py`, `backends/sevenzip_reader.py`, `backends/rar_reader.py` | **F2** | **DONE (2026-07-24, #184)** — one `_drive_pass_streams` (`base_reader.py:450`), all backends call it |
| **S2** | Member-list pipeline half-unified: shared stamper/publication landed, but dual drive loops + **mirrored double-fault guards** remained | `base_reader.py` finalize helpers | **F2** | **DONE (2026-07-24, #184)** — one `_finalize_pass_links` (`base_reader.py:1100`); guards collapsed |
| **D4** | `open-issues.md` contradicts its own bucket rules (P1 decided+implemented yet listed as to-fix; dead change-path refs) | `docs/internal/open-issues.md:34-55,185` | **F2** | **PAY** — 15-min sweep |
| **D5/D6** | Lifecycle housekeeping: `stop-on-failure-not-policy` complete-but-unarchived; `cli-product/` review done-pending-archive | `openspec list`; `review/STATUS.md` | **F2** | **DONE** (2026-07-20) — OpenSpec → `archive/2026-07-20-stop-on-failure-not-policy/`; review → `archive/2026-07-20-cli-product/` |
| **T7** | Corpus matrix thin spots post-oracle-retirement: ISO only in `basic`; encrypted-header 7z / multi-volume outside the nets | `tests/sample_archives.py:307-345` | **F2** | **PAY** — half-day audit + cheap extensions |
| **DD4** | rapidgzip ISIZE truncation backstop ships self-describedly under-characterized (change now at **1/13**) | `openspec/changes/rapidgzip-truncation-investigation/` | **F2** | **PAY before 0.2.0** (Q4 decided 2026-07-20); still deferred — see change `design.md` |
| **D5/D6→N3** | Lifecycle: two ✓ Complete changes unarchived (`unify-pass-driver`, `gzip-zlib-truncation-recovery`) | `openspec list` | **F2** | **PAY (2026-07-24)** — `openspec archive` sweep; sync main specs first |
| **T4** | Free-threaded CI core-only; no multithread `members_report_if_available` test | `ci.yml:168-191` | **F2** | **KEEP scope** (already published honestly) / **PAY one test** |
| **DD7/DD8** | CLI `--json` (wait for schema) and `--raw` quoting remainder | cli-product Q2/Q4 | **F2** | **KEEP** — decided/additive; space reserved |
| **DD9–DD12** | Threat-model residuals (O1/O6/O7/O8), C3 fidelity, api-coherence Q5, C4 scope | registers | **F1-F2** | **KEEP** — all additive, all already recorded with rationale |
| **T5/T6** | Remaining fault injections (symlink-EPERM, raw ENOSPC); no stateful concurrency stress | tests.md | **F1** | **KEEP** recorded; pay opportunistically |
| **DD5** | `seekable-gzip-and-block-writing` (0/24, spec-only, Phase 8) | in-flight change | **F1** | **KEEP** — post-0.2.0 by plan |
| **S1/S4/S5/S6** | Error boundary paid & holding; ReaderState reworked cleanly; module splits earning seams; one in-code marker; `VerifyingStream` leftover parked | structural.md | — | **fine** (S1 residue + VerifyingStream: KEEP) |

## The pre-0.2.0 pay list, in order

> **Superseded 2026-07-24** — S2/S3/T1 are now paid and #171 unblocked D1's
> enforcement wording. See the [revised pay list](#update--2026-07-24) at the top.
> Original list preserved below for provenance.

1. **D1** — re-word the perf claim (after deciding Q1/Q2 in `QUESTIONS.md`).
2. **D2** — write `SECURITY.md`.
3. **D3** — start `CHANGELOG.md`.
4. **T1 + T2** — widen the generative nets (solid-RAR mutation; lzip/`.Z`
   seek property test). Cheap, reuses machinery, and T1 doubles as the
   safety net for `unify-pass-driver` (S2+S3).
5. **T3** — benchmark-gate RAR/encrypted/accelerator cases (D1 dependency).
6. **D4 + T7** — the housekeeping sweep (open-issues, corpus-matrix audit).
   (**D5/D6** archived 2026-07-20.)
7. **S2+S3 / `unify-pass-driver`** — pay before 0.2.0 (Q3 = b); proposal in
   `openspec/changes/unify-pass-driver/` (do **not** add PLAN/IDEAS entry-gate language).
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
