# Brief — the pre-`0.2.0` debt ledger (backlog Topics 4 + 5)

Commissioned 2026-07-20 against `main` @ `7bb862b`. This is the combined
"quality & debt" pass that `backlog.md` scoped as Topics 4 (test-suite
strategy) and 5 (structural cleanliness), run together per its guidance.
**Ledger refreshed 2026-07-23** against `main` @ `8cc3ea5` (mark DONE/in-flight
against post-#169 landings; no new product code in the refresh).

## The ask

Produce the pre-`0.2.0` **debt ledger**: every structural shortcut, duplication
(especially the S2/S3 pass-driver unification now that native RAR exists),
doc ↔ spec ↔ code drift, deferred decision, and test-strategy hole that would
not catch the class of bugs prior reviews found. For each item: **pay before
0.2.0, or keep with an explicit justification**. Rank by "freezes at release"
cost.

## Framing (from `backlog.md`)

The maintainer's goal is **zero deliberately-carried debt** — not "clean
enough". The deliverable is a ledger with a pay/keep verdict per item, not a
vibe. The value of doing this *before* `0.2.0` is specific: after the release,
some of these (public surface, specs, published claims) stop being free to
change.

## Scope

- Verify the current state of the deferred `deep-simplification` items
  (S1 one-error-boundary honesty in the paths added since; S2 member-list
  pipeline; S3 pass driver — RAR has landed, so the predicted fourth copy is
  measurable, not hypothetical; S4 ReaderState).
- Module-split coherence after ~25 archived OpenSpec changes; dead code;
  remaining `TODO`/"deferred" markers in `src/`.
- Doc ↔ spec ↔ code drift: user docs, live OpenSpec specs, threat-model
  register, `open-issues.md`, review lifecycle files, published claims
  (VISION perf budget).
- Deferred decisions: open review QUESTIONS, in-flight OpenSpec changes,
  threat-model residuals — each needs a recorded pay/keep verdict.
- Test strategy: would the suite catch the bug classes prior reviews found
  (seek-math regressions, demux misalignment, fault-injection gaps, the
  format×feature matrix after oracle retirement)?

## Conventions

Inherits `review/README.md` conventions. This review is **analysis-only** (no
code changes); findings cite `file:line` on `main` @ `7bb862b`. Baseline:
working tree clean; `openspec validate --all` green (27/27 items, CLI 1.6.0);
the test suite was not rerun for this pass since no code changed — behavioural
claims come from reading code and the committed review/CI artifacts, and are
marked "verify" where a run would be needed to confirm.
