# Questions for the maintainer

Decisions this ledger cannot make unilaterally (pause-and-ask rule). Everything
else in the ledger carries a recommended verdict the maintainer can simply
accept or flip.

## Q1 — Performance Q2 (wall-budget enforcement) is now blocking the release's honesty, not just the harness — **DECIDED (2026-07-20)**

**Decision: (a).** Nightly wall-ratio drift vs the previous successful
`benchmark-wall` JSON artifact (`benchmark-wall.yml` +
`--wall-drift-baseline`); quiet days re-publish that artifact (preserving
`measured_at`); full re-measure at least every ~30 days. Absolute VISION bands
remain informational; peers `(c)` already in harness. Unblocks D1 wording: CI
enforces structural axes on every PR and wall-ratio *drift* on nightly — not
absolute ≤1.3×.

`review/performance/QUESTIONS.md` Q2 has been open since 2026-07-18. The
ledger's D1 (re-word the ≤1.3× claim) can land its *band* changes without Q2,
but the sentence "this budget is enforced in CI" vs "measured and published"
depends on it. Options as the perf review framed them: (a) nightly
drift-vs-previous-JSON, (b) nightly 2× band on read_all only, (c) stay
informational. **A choice — any choice — is needed before the VISION/costs
re-wording can be finished.** Recommendation: (a), it dodges shared-runner
absolute-ratio flake and catches regressions, which is what the claim needs.

## Q2 — ZIP open+list is above the maintainer's own re-scoped band: land L5 pre-release, or publish the real number? — **DECIDED (2026-07-20)**

**Decision: (b).** Peer-ratio bands are **aspirational**; publish measured
numbers in `docs/costs.md` / VISION; L5 (lazy `ArchiveMember` derivation) is a
named post-0.2.0 follow-up in `IDEAS.md`. Measured many-small ZIP ~3.7–4× and
7z ~2× are good enough for everyday use — do not block the release on L5.

Post-#143/#146, ZIP open+list sits ~3.7–4× vs the Q1-direction band of 2–3×;
7z ~2.0–2.2× vs ≈1.25× native-par. Two honest paths were:

- **(a)** Commission **L5** (lazy `ArchiveMember` derivation — needs an
  OpenSpec change; the remaining cost is `_to_member` + registration per the
  listing attribution) before 0.2.0 and try to enter the band; or
- **(b)** Ship 0.2.0 with the band documented as *aspirational* and the
  measured numbers published beside it (costs.md table), L5 as the named
  follow-up.

**(b) landed** with this change (refreshed onto main 2026-07-24).

## Q3 — S2+S3 unification: accept "entry gate for the next backend", or pay pre-release? — **DECIDED (2026-07-20)**

**Decision: (b) pay before 0.2.0.** Unify S2+S3 as one OpenSpec change
(`unify-pass-driver`) now — clean structure preferred over shipping with four
divergent pass-driver copies; the suite is the regression net. T1 (solid-RAR
mutation) lands first as the safety net. **Rejected:** (a) entry gate for the
next backend (do not add PLAN/IDEAS entry-gate language).

The ledger's recommended verdict (structural.md) had been: carry the four-copy
pass driver through 0.2.0 and gate the next backend. Maintainer overrode toward
ledger purity / release-risk tolerance in favor of paying now.

## Q4 — Is `rapidgzip-truncation-investigation` allowed to ride past 0.2.0? — **DECIDED (2026-07-20)**

**Decision: PAY before 0.2.0.** Do not ship the under-characterized ISIZE
backstop as release-done. Measurement + narrow/extend/remove stay in
`openspec/changes/rapidgzip-truncation-investigation/` (enriched with
`design.md`); implementation is a later PR, not triage.

The shipped ISIZE backstop is, by its own proposal's words, "a heuristic built
on incomplete knowledge of rapidgzip's actual behavior", and the
characterization change is 1/11 tasks done. The ledger's earlier lean was KEEP
(accelerators opt-in, AUTO gated, threat model scopes accelerators out,
refining later is non-breaking). Maintainer overrode: knowingly-heuristic guard
on a supported path is unacceptable release debt even when opt-in.

## Q5 — Does a `CHANGELOG` entry-zero get written now?

D3 says PAY. The only real question is form: a conventional
`CHANGELOG.md` starting at 0.2.0 ("initial public release" + highlights), or
generated release notes only. Recommendation: a committed `CHANGELOG.md` —
the adoption capstone explicitly looks for one in-repo.
