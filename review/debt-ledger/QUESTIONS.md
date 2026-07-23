# Questions for the maintainer

Decisions this ledger cannot make unilaterally (pause-and-ask rule). Everything
else in the ledger carries a recommended verdict the maintainer can simply
accept or flip. **Refresh 2026-07-23:** Q1/Q3/Q4 decided and (where applicable)
implemented; Q2/Q5 still open but embodied in open PRs.

## Q1 — Performance Q2 (wall-budget enforcement) — **DECIDED + DONE (2026-07-20)**

**Decision: (a).** Nightly wall-ratio drift vs the previous successful
`benchmark-wall` JSON artifact (`benchmark-wall.yml` +
`--wall-drift-baseline`). Absolute VISION bands remain informational; peers
already in harness. Landed in **#171**. Unblocks D1 *enforcement* wording;
band honesty is still D1/Q2.

## Q2 — ZIP open+list is above the maintainer's own re-scoped band: land L5 pre-release, or publish the real number?

Post-#143/#146, ZIP open+list sits ~3.7–4× vs the Q1-direction band of 2–3×;
7z ~2.0–2.2× vs ≈1.25× native-par. Two honest paths:

- **(a)** Commission **L5** (lazy `ArchiveMember` derivation — needs an
  OpenSpec change) before 0.2.0 and try to enter the band; or
- **(b)** Ship 0.2.0 with the band documented as *aspirational* and the
  measured numbers published beside it (costs.md table), L5 as the named
  follow-up.

Recommendation: **(b)**. Open PR **#172** implements that lean (aspirational
peer bands + published measured ratios). **Accepting #172 ≈ deciding (b).**
Still needs an explicit maintainer merge (or flip to (a) and close #172).

## Q3 — S2+S3 unification — **DECIDED + DONE (2026-07-20 / #184)**

**Decision: (b) pay before 0.2.0.** Landed as OpenSpec `unify-pass-driver`
(#184) with T1 solid-RAR mutation first. Change is ✓ Complete; archive under
ledger **D7**.

## Q4 — Is `rapidgzip-truncation-investigation` allowed to ride past 0.2.0? — **DECIDED (2026-07-20); implementation still open**

**Decision: PAY before 0.2.0.** Change enriched (#175); Linux characterization
+ recommended extend ISIZE backstop is in open PR **#177** (was CONFLICTING at
refresh — rebase). Stdlib gzip recoverable truncation (#183) is adjacent and
**does not** satisfy this decision.

## Q5 — Does a `CHANGELOG` entry-zero get written now?

D3 says PAY. Form options: committed `CHANGELOG.md` starting at 0.2.0, or
generated release notes only. Recommendation: committed `CHANGELOG.md`.
Open PR **#176** implements that lean but was CONFLICTING at refresh —
**rebase + merge ≈ deciding the form.** Still needs an explicit maintainer
merge (or flip to generated-only and close #176).
