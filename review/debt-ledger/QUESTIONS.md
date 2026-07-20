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

## Q2 — ZIP open+list is above the maintainer's own re-scoped band: land L5 pre-release, or publish the real number?

Post-#143/#146, ZIP open+list sits ~3.7–4× vs the Q1-direction band of 2–3×;
7z ~2.0–2.2× vs ≈1.25× native-par. Two honest paths:

- **(a)** Commission **L5** (lazy `ArchiveMember` derivation — needs an
  OpenSpec change; the remaining cost is `_to_member` + registration per the
  listing attribution) before 0.2.0 and try to enter the band; or
- **(b)** Ship 0.2.0 with the band documented as *aspirational* and the
  measured numbers published beside it (costs.md table), L5 as the named
  follow-up.

Recommendation: **(b)**. L5 restructures the hottest member-model path right
before release; the honest-number option costs nothing and the claim stays
falsifiable-but-true. But this is a positioning call — VISION is the
maintainer's document.

## Q3 — S2+S3 unification: accept "entry gate for the next backend", or pay pre-release?

The ledger's recommended verdict (structural.md) is: carry the four-copy pass
driver and the split materialization/progressive drive loops **through**
0.2.0, and make their unification a hard *entry gate* for the next backend
(native streaming ZIP), recorded in `PLAN.md`. The alternative reading of the
zero-debt goal is that 0.2.0 itself should ship debt-free and this is the
best-motivated structural item on the books (S3 predicted the RAR copy; it
happened). Both are defensible; the difference is release risk vs ledger
purity. Which does the maintainer want? (If pre-release: do S2+S3 as one
OpenSpec change, guarded by the T1 mutation-net extension landing *first*.)

## Q4 — Is `rapidgzip-truncation-investigation` allowed to ride past 0.2.0?

The shipped ISIZE backstop is, by its own proposal's words, "a heuristic built
on incomplete knowledge of rapidgzip's actual behavior", and the
characterization change is 1/11 tasks done. The ledger's verdict (DD4) is KEEP
for 0.2.0: accelerators are opt-in, AUTO is gated on verifiable sizes, the
threat model excludes accelerators from the defended surface, and refining the
backstop later is non-breaking. Confirm — or, if the maintainer considers a
knowingly-heuristic guard on a shipped path unacceptable debt for a release,
the characterization (a measurement matrix, no design work) is the pay path.

## Q5 — Does a `CHANGELOG` entry-zero get written now?

D3 says PAY. The only real question is form: a conventional
`CHANGELOG.md` starting at 0.2.0 ("initial public release" + highlights), or
generated release notes only. Recommendation: a committed `CHANGELOG.md` —
the adoption capstone explicitly looks for one in-repo.
