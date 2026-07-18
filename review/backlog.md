# Review backlog — deferred review ideas

Non-security review topics worth doing, but *after* the in-flight round
(`api-coherence`, `cli-product`, `performance`, `stream-layering`). They differ in
character and timing:

- **Topics 4 + 5** (test-strategy, structural-cleanliness) — a single lighter
  "quality & debt" pass; they overlap heavily and both feed the same pre-`0.2.0`
  polish, so run them together rather than as two frontier deep dives.
- **Topic 6** (decode-engine performance) — a later *performance* round, once the
  `stream-layering` wrapper work has landed; mostly independent of it.
  *(stream-layering fusion landed in #137 — Topic 6 is unblocked on that axis.
  Also absorb parked stream-layering Q4: real `SlicingStream.readinto`.)*
- **Topic 7** (outside-in adoption / confidence) — a **capstone**, meaningful only
  *after everything else is fully addressed*; it judges the finished library, not a
  work in progress.

Live triage of the *current* in-flight round's remaining items is in
[`STATUS.md`](STATUS.md) (actionable / needs-decision / future archive-copy).

When commissioned, each gets its own top-level directory with a `brief.md` and
archives when addressed (see `README.md`).

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

## Topic 6 — Decode-engine performance (`DecompressorStream` / `Decoder`)

**Why:** the archived stream-decoder review (PR #122) and the #96 composition refactor
looked at the decode engine for **correctness and clarity**, not performance — and the
in-flight `stream-layering/` review deliberately scopes the decode engine *out*, owning
only the wrapper stack (slice/verify/outer) around it. So the per-chunk cost of the
decode engine itself is unreviewed. Mostly independent of `stream-layering`, so it's a
separate later performance round (after that one lands, so the two don't churn the same
code at once).

Concrete surfaces to measure:
- Per-`read()` dispatch through `DecompressorStream` → `Decoder` and the base's
  `_read_decompressed_chunk` buffering (the archived F3 memory-bound fix touched this —
  is the *steady-state* read cost tight now?).
- `fix_stream_start_position` adding a **second** `SlicingStream` in front of a codec that
  assumes `tell()==0` — is that slice avoidable on the common path?
- The accelerator wrappers (`_AcceleratorStream` / `_GzipTruncationCheckStream`) per-chunk
  overhead vs the raw rapidgzip handle, and whether the AUTO gate's crossover is where the
  fused cost actually breaks even (coordinate with `performance/`'s gate findings).
- `readinto` zero-copy through the decode stack (same lens as `stream-layering`, one layer
  down): does decoded output get copied more than necessary?

Not a re-litigation of the #96 design — a pure "is the decode read path as cheap as it can
be" pass, with numbers.

## Topic 7 — Outside-in: adoption & confidence (capstone)

**Why:** every other review looks *inward* (is this code correct / clean / fast). This one
looks *from the outside*: would someone actually adopt archivey, and what's missing to make
them confident? Run it **last** — it judges the finished library against its competitors and
its own VISION promises, so it's only meaningful once the correctness/API/perf/CLI work is
fully addressed. Two framings, usable together:

- **The adopting engineer / company (primary).** Put the reviewer in the shoes of an
  external engineer evaluating archivey against the alternatives (`zipfile`/`tarfile` +
  ad-hoc glue, `libarchive` bindings, `py7zr`/`rarfile`, `patool`, shelling out to
  `7z`/`unrar`). What's missing for **confidence and peace of mind** to depend on it: API
  stability guarantees and semver, the security/CVE-surface story made legible, a
  trustworthy changelog/release cadence, benchmarks a skeptic can rerun, documentation that
  answers "how do I do X safely," licensing/provenance of vendored code, supported-platform
  and free-threading matrices, "what happens on damaged/hostile input" stated plainly,
  responsiveness signals. Deliverable: the concrete gaps between "technically excellent" and
  "a stranger bets a production pipeline on it," ranked.
- **The CPython maintainers (a high bar, not a goal).** As an explicit stretch lens — *not*
  an actual objective — assess it as if stdlib inclusion were on the table: API taste and
  minimalism, zero-surprise cross-platform behaviour, test rigor, security posture,
  maintenance burden, backwards-compat discipline, the "does this belong in the standard
  library" bar. Useful precisely because it's a harsher standard than any real adopter would
  apply, so it surfaces polish gaps the primary framing might accept.

This is judgement + gap analysis, not a bug hunt — closer to a product/positioning audit
grounded in the code and docs. It likely produces roadmap items, not fixes.

## Not a review — a feature gap to track separately

**Salvage / best-effort read mode** (old `roadmap.md`, `IDEAS.md`) — the "founding use
case" (truncated archive → every recoverable member + an honest error) is still
unbuilt; reads are all-or-error. This is a feature to *design and propose* (an
OpenSpec change), not something a review finds. Flagged here only so it doesn't get
lost among the review topics — it likely outranks both topics above in product value,
and `--salvage` is already reserved in the CLI grammar waiting for it.
