# Review backlog — deferred to a lighter follow-on pass

Two more non-security topics are worth a review, but *after* the in-flight three
(`api-coherence`, `cli-product`, `performance`). They overlap heavily, both feed the
same pre-`0.2.0` polish, and neither is a deep hostile-input or design-freeze
question — so they're better run together as one lighter "quality & debt" pass than
as two separate frontier deep dives. When commissioned, each gets its own top-level
directory with a `brief.md` and archives when addressed (see `README.md`).

## The framing: zero tech debt

The maintainer's goal for this project is to keep it **debt-free** — not "clean
enough," but *zero* deliberately-carried debt. That reframes a "cleanliness" review
from cosmetic to a **debt ledger**: enumerate every known shortcut, duplication,
drift, and deferred decision, and for each one force the choice — *pay it now
(before the public API freezes) or record it as an explicit, justified decision.* The
deliverable is that ledger with a pay/keep verdict per item, not a vibe.

The value of doing this *before* `0.2.0` is specific: after the release, some of
these (public-surface, spec) stop being free to change.

## Topic 4 — Test-suite strategy / coverage architecture

**Why:** all three security reviews repeatedly concluded *"no test in the suite
catches this."* That's a signal about the test *strategy*, not three isolated gaps. A
meta-review of how the suite is built — where example-based tests should be property
or fuzz tests, whether the declarative corpus covers the format×codec×config matrix,
where fault injection is thin — is due; the old `archive/2026-07-12-codebase-deep-review/tests.md`
predates +5.5k LOC, native RAR, native ZIP codecs, and the CLI.

Concrete already-known gaps to fold in (don't re-derive):
- **No randomized/property seek test** — archived stream-decoder **F5** (also old
  review finding #6). Every seek test reads forward-to-EOF before seeking, the one
  ordering that hid the F1 crash. A seek-math property test would have caught it.
- **No "truncated read through both `read(-1)` and chunked idioms" test** — stream
  **F4** root cause; the deferred-error path is only exercised one way.
- Free-threaded coverage runs core-only; the ISO/accelerator support boundary is
  implicit in a CI flag (old `roadmap.md`).
- Oracle retirement (#46) fallout — is the declarative corpus now the sole guard, and
  is its matrix complete for the formats added since?

## Topic 5 — Structural cleanliness (the debt ledger)

**Why:** the old `deep-simplification` pass proposed three category-deleting
structural changes and they were **deferred, not rejected**:
- **S1 — one error boundary.** `_translated_errors` was applied to the original
  backends but S1's full "backends never hand-roll translate/stamp/raise" was left;
  check whether RAR (which routes through the shared boundary — good) and the newer
  paths kept it honest, and whether the ~10-sites duplication is actually gone.
- **S2 — one member-list pipeline** (materialized + progressive unified). Deferred.
- **S3 — one pass driver.** Deferred — and S3 *explicitly predicted* that the native
  RAR reader would add a fourth copy of the "close-previous / open-current / yield /
  cleanup" loop. **RAR has now landed**, so this duplication is concrete and
  measurable today rather than hypothetical. This is the single best-motivated item
  in the ledger.

Plus the mechanical debt a zero-debt pass should sweep:
- Module-split coherence after ~25 archived OpenSpec changes (`internal/config` vs
  `config`, `extraction_types`, `sevenzip_methods`/`pipeline`, `timestamps`) — is each
  split earning its seam?
- Dead code / unused exports (overlaps `api-coherence`'s surface audit — coordinate).
- **Doc ↔ spec ↔ code drift**: with ~25 changes archived and specs synced repeatedly,
  are the user docs, the OpenSpec live specs, and the code still telling one story?
  (The old review found docstring/spec mismatches; the surface has churned a lot since.)
- Any remaining `TODO`/`FIXME`/"deferred"/"follow-up" markers in `src/` — each is a
  debt-ledger line by definition.

## Not a review — a feature gap to track separately

**Salvage / best-effort read mode** (old `roadmap.md`, `IDEAS.md`) — the "founding use
case" (truncated archive → every recoverable member + an honest error) is still
unbuilt; reads are all-or-error. This is a feature to *design and propose* (an
OpenSpec change), not something a review finds. Flagged here only so it doesn't get
lost among the review topics — it likely outranks both topics above in product value,
and `--salvage` is already reserved in the CLI grammar waiting for it.
