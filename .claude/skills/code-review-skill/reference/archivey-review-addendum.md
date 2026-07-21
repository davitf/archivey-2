# Archivey Review Addendum

> **This is a repo-specific focus doc layered on top of the generic code-review skill.**
> Use the skill’s process, severity labels, and Python/quality guides as the base;
> use **this file** for what archivey uniquely cares about.
>
> **Review order:** archivey PRs use **code first, then context** (§8). That overrides
> the skill’s Phase 1 “read the design narrative before the diff.”
>
> Do not merge these rules into the upstream-derived guides — keep the delta visible.

**Authoritative sources (read these when a finding touches them):**

| Source | Role |
|--------|------|
| [`VISION.md`](../../../../VISION.md) | Product tie-breaker when trade-offs conflict |
| [`CONTRIBUTING.md`](../../../../CONTRIBUTING.md) | Coding, typing, exceptions, testing, three-config gate |
| [`openspec/specs/`](../../../../openspec/specs/) | Capability contracts — starting point for behavior, revisable when wrong (§3) |
| [`docs/internal/threat-model.md`](../../../../docs/internal/threat-model.md) | Trust boundaries + open security gaps |
| [`review/README.md`](../../../../review/README.md) | Deep-review conventions, ranking, deliverable shape |
| [`review/STATUS.md`](../../../../review/STATUS.md) | Live triage of in-flight review follow-ups |

---

## 0. Finding discipline (how to report)

Archivey reviews optimize for **maximum code quality with a human maintainer as the
filter** — not for an automated gate that must minimize false positives. So
**over-report on existence, be rigorous on labeling.** Raise the concern; never suppress
a real one because you're unsure. The discipline is *honest labeling*, not silence.

Output stays **markdown prose** (portable across Cursor / Claude Code / others). Do not
route findings through a host-specific findings tool — the two-axis + reclassification
model below is richer than those schemas, and prose is the source of truth.

### Two axes: severity ≠ confidence

Rate every finding on two independent axes so the maintainer can read
**severity × confidence** and decide:

- **Severity** — impact *if the finding is real*: 🔴 `[blocking]` / 🟡 `[important]` /
  🟢 `[nit]` (plus 💡 / 📚 / 🎉 non-blocking). See §7.
- **Confidence** — how well you traced it:
  - `CONFIRMED` — you traced the actual failing path (definitions, callers, guards).
  - `PLAUSIBLE` — real risk, not fully traced or no repro built.
  - `DISPROVEN` — you traced it and the code is actually correct. **Do not delete it** —
    route it (below).

Low confidence lowers the *confidence tag*, never the decision to report. A
🔴 / `PLAUSIBLE` finding is still reported.

### Verification routes findings; it never silently culls them

After the code + context passes, re-trace each candidate (§8 "trace, don't
pattern-match"). Tracing changes the tag and may *reclassify* — it does not delete real
concerns. When a finding comes back `DISPROVEN`, ask *"why did I, reading carefully,
think this was broken?"*:

- A careful reader could reasonably have misread it → the code isn't self-documenting.
  Re-file as 🟡 **clarity / doc-debt** (§8's documentation-debt rule) — a comment,
  clearer name, or an `assert` that encodes the invariant.
- Tracing revealed a genuine but non-obvious invariant → 💡 / 📚: suggest the comment or
  assertion that would have made it obvious.
- It was a careless misread ordinary attention would have avoided → drop it; if it still
  cost real review effort, a 🟢 nit is fair.

Only a careless self-misread is ever dropped. Everything else becomes a (possibly
smaller) finding.

### Failure scenario: requested, not gating

Try to name a concrete trigger for every finding — input / archive / state → wrong
result, crash, or contract violation:

- `CONFIRMED` + repro → flag it as a **red–green regression-test candidate**
  (CONTRIBUTING wants red–green for bug fixes, §4).
- Can't build one → the finding still stands, tagged `PLAUSIBLE` / needs-repro. A missing
  repro lowers confidence, never existence.

### Keep it scannable

Over-reporting fails only when it is *unlabeled*. Hold the noise down by discipline, not
suppression:

- **Dedupe by root cause** — one finding per cause; cite one site, list the rest.
- **Rank** by severity, then confidence within a tier.
- Every finding carries: severity, confidence tag, location (`file:line`), why it
  matters, a fix direction, and (where possible) a trigger.

---

## 1. What you are reviewing

Archivey is a **sync-first, zero-dep-core Python library** for reading, streaming, and
safely extracting archives (ZIP / TAR / ISO / directory / single-file codecs; native
7z/RAR). There is no web UI, SQL/ORM, or HTTP product surface. The CLI is a **wedge
and second consumer** of the library API — useful evidence of API gaps, not the main
act (`VISION.md`).

### Load-bearing claims (rank findings against these)

From `VISION.md` / `review/README.md` — a finding that undercuts a marketing claim
outranks a same-severity nit that does not:

1. **One uniform interface** with honest cost / capability signals (no silent
   per-format guesses).
2. **Safe by default** — zip-slip, symlink escape, and decompression bombs require
   explicit opt-out; safety is a contract, not a flag.
3. **Memory-safe parsing of hostile input** — pure-Python parsers preferred so crafted
   archives cannot *corrupt* native parser memory; failures must be honest errors.
4. **Damaged input is first-class** — recoverable members + an honest error beat a
   bare exception at open (salvage mode is backlog; don’t invent it in a PR review).
5. **≤ ~1.3× stdlib** on common ZIP/TAR open/list/read/extract paths (up to ~2× when
   safety/correctness justifies it). Track **bytes decompressed and seeks**, not only
   wall time — silent solid-block re-decode fails the budget even if a tiny fixture hides it.

### Non-goals (don’t demand these in reviews)

- Async public API in v1
- Compatibility shims for `zipfile` / `py7zr` / `rarfile` APIs
- Quirk-driven architecture that lets third-party reader quirks leak into core contracts
- In-place archive modification / encryption-write for 7z/RAR

---

## 2. “No surprises” — the standing design rule

Behavior differences between formats must be **data** (`None`, explicit enums,
documented sentinels) — never silent guesses (`openspec/project.md`, `VISION.md`).

When reviewing cross-backend changes, ask:

- [ ] Does every backend give this field the **same meaning**?
- [ ] If a backend cannot provide it, is emptiness / `None` documented and asserted?
- [ ] Would a caller branching on the field trip on a format-specific surprise?
- [ ] Should the declarative corpus / conformance sweep grow an assertion?

Parity hot spots from past reviews: `member.hashes`, `ListingCost` / `AccessCost`,
`MemberStreams` / `StreamCapability`, timestamps/mode/links/`MemberType` (incl. `ANTI`),
duplicate-name / `is_current` semantics.

---

## 3. Coding & contract checks (`CONTRIBUTING.md`)

These are **review blockers** when violated — not style nits.

### Zero-dep core & extras

- [ ] Core / native 7z read / RAR metadata import **no** third-party packages at runtime
- [ ] New deps land only as optional extras and match `packaging-and-extras`
- [ ] Optional imports are lazy at the right boundary (don’t pull extras into core import)

### Types

- [ ] Public API and anything feeding it is typed; `py.typed` story preserved
- [ ] Both **Pyrefly and ty** stay clean (not mypy/pyright)
- [ ] `# type: ignore` / checker suppressions are **specific**, rare, and **reasoned**
  inline — unjustified suppressions are blocking

### Exception translation

- [ ] Archive problems surface as `ArchiveyError` subclasses via the reader translator
- [ ] Known third-party errors map to the right type (`CorruptionError`,
  `TruncatedError`, `EncryptionError`, …)
- [ ] **No catch-all** `except Exception` that converts unknowns — return `None` from
  the translator and let unrecognized exceptions propagate
- [ ] `OSError` / `KeyboardInterrupt` / `MemoryError` propagate unless a spec says
  otherwise (e.g. safe-extraction `OnError.CONTINUE`)
- [ ] `ArchiveyUsageError` stays **outside** the archive-error tree (caller misuse)

### Zero tech debt (and clean-as-you-go)

The project aim is **debt-free** — not “clean enough,” but *no deliberately carried
debt* (`review/backlog.md`). Clean-as-you-go is how day-to-day PRs enforce that:

- [ ] Touched code is left in the shape it *should* have (rename / move / small
  refactor in the same change when the design requires it)
- [ ] Don’t land a “we’ll clean this later” shortcut without an **explicit, justified
  decision** (PR note, `QUESTIONS.md`, `IDEAS.md`, or `review/backlog.md`) — unspoken
  deferrals are debt
- [ ] Duplication, drift, and TODOs introduced or left adjacent to the change are
  either **paid now** or recorded as keep-with-reason — not ignored
- [ ] **Pause and ask** on real design discrepancies — do not silently pick a winner
  (`CONTRIBUTING.md`, `CLAUDE.md`, `review/README.md`)

### Specs & OpenSpec changes

Specs are **guidelines for intended behavior**, not holy writ. Reviewers and authors
should treat them as the best current description of the contract — and revise them
when reality or a better design wins.

- [ ] **Not every change needs a spec.** Bug fixes, refactors, tests, tooling, docs
  polish, and internal cleanups usually do not. Prefer a spec/`openspec/changes/`
  delta when the **public or cross-format behavior contract** moves (or when an
  in-flight change proposal already owns the work).
- [ ] When a change *does* move a contract, update the relevant
  `openspec/specs/` (or propose via `openspec/changes/`) and matching user/decision
  docs in the **same** change — don’t leave prose lying.
- [ ] **If following a spec yields a worse outcome**, don’t contort the code to satisfy
  the letter of the doc. Surface it: prefer changing the spec (or opening a change
  proposal / maintainer question) so the written contract matches the better design.
- [ ] Spec ↔ doc ↔ code conflicts still use **pause-and-ask** — guessing bakes the
  wrong decision in. The goal is an explicit revision, not silent divergence.
- [ ] Open threat-model gaps (`O*`) are not “fixed” by marketing language alone

### Comments

- [ ] Explain *why* (format quirks, hostile-input edges), not narrate *what*
- [ ] Resulting code is self-explanatory (`CONTRIBUTING.md`); OpenSpec / PR prose is not
  the only explanation — future editors see the tree, not the diff
- [ ] Links to specs / decisions / explorations / OpenSpec changes are fine for complex
  decisions — but an inline summary should usually carry the *why*
- [ ] Match surrounding comment density

---

## 4. Testing expectations

- [ ] Prefer **behavior** assertions on the public API; unit-test stream/parser/codec
  internals when they are shared foundations
- [ ] Corrupt, truncated, encrypted, wrong-password, empty members, weird names,
  non-seekable sources are in scope — especially when touching readers/translators
- [ ] Use the **declarative corpus** / conformance sweep where format×shape coverage
  matters (`testing-contract`)
- [ ] Bug fixes: **red–green** — failing repro first, then fix
- [ ] Say which dependency config a finding needs: `[all]`, `[all-lowest]`,
  `[core-only]` (`CONTRIBUTING.md`)
- [ ] Format before commit (`ruff`); don’t bike-shed formatting in review

Past review lesson: “no test in the suite catches this” is often a **strategy** gap
(property/fuzz/fault-injection), not only a missing example — flag thin coverage
honestly (`review/backlog.md` Topic 4).

---

## 5. Domain checklist (PR-sized)

Use alongside the skill’s generic checklist. Severity: 🔴 blocking / 🟡 important /
🟢 nit — same labels as the skill.

### Safety & hostile input

- [ ] Extract paths: traversal, absolute/UNC, null bytes, symlink/hardlink escape,
  never-write-through-symlink (`threat-model`, `safe-extraction`)
- [ ] Bomb / resource limits: output caps, ratios, entry counts, listing limits where
  applicable
- [ ] Parser bounds: huge length/count fields from headers cannot OOM the process
- [ ] Subprocess (`unrar`, fixture `7z`, …): list args, no `shell=True` interpolation
- [ ] Passwords / key material absent from logs, `repr`, and exception messages

### Streaming, cost model, performance

- [ ] Hot paths stream; avoid slurp-then-parse unless justified
- [ ] Solid / multi-member access does not **silently** re-decompress the same block
- [ ] Cost signals (`ListingCost` / `AccessCost`) stay honest if behavior changes
- [ ] Prefer stored digests (`member.hashes`) over decompress-to-hash when the format
  provides them
- [ ] Perf claims cite bytes/seeks or existing `benchmarks/` — not vibes

### API & layering

- [ ] Public vs `internal/` boundary respected (CLI reaching into `internal/` is a
  smell — often an API gap; see `review/api-coherence/`)
- [ ] New exports are intentional freeze surface; don’t grow `__all__` casually
- [ ] Format backends stay behind the uniform reader contracts
- [ ] Sync-first: no accidental async public API

### Specs & docs (quick)

- [ ] Spec update only when the behavior contract moves — see §3
- [ ] Don’t reject a better design solely because an old spec forbids it; propose
  revising the spec instead
- [ ] Don’t demand a new OpenSpec change for pure refactors / bugfixes with no
  contract delta

---

## 6. Deep reviews (`review/`) — when the skill expands into a brief

For commissioned deep reviews (not ordinary PR review), inherit
[`review/README.md`](../../../../review/README.md):

1. **Baseline first** — record green gates (pytest / skips, pyrefly, ty, ruff) and
   which dependency config.
2. **VISION ranking** — order findings by load-bearing claims (§1).
3. **Deliverable shape** — `SUMMARY.md` (headline + severity table + status), theme
   files, `QUESTIONS.md` for maintainer decisions, and a **“what is actually fine”**
   section.
4. **Evidence** — `file:line`, concrete triggering input/state, runnable repro when
   practical.
5. **Pause and ask** — spec/design conflicts go to `QUESTIONS.md`, not silent fixes
   (including “the spec is wrong; here’s the better contract”).
6. **Don’t re-litigate settled ground** — check archive tables + `STATUS.md` for
   already-closed findings before spending budget.
7. **Archive lifecycle** — only move a review to `review/archive/` when every
   actionable item is fixed or consciously deferred (`STATUS.md` / `backlog.md`).

In-flight themes to know (see `STATUS.md` for live items):

| Review | Lens |
|--------|------|
| `api-coherence/` | Uniform interface, surface size, CLI-as-consumer gaps |
| `performance/` | ≤1.3× budget, gate efficacy, solid/listing hotspots |
| `stream-layering/` | Wrapper correctness + collapse (largely done) |
| `cli-product/` | CLI UX / grammar / exit codes (product, not correctness) |
| Archived security round | Hostile input, crypto, RAR, stream decoder |

---

## 7. Severity mapping for this repo

| Label | Archivey examples |
|-------|-------------------|
| 🔴 `[blocking]` | Path escape on default extract; catch-all exception translation; core grows a hard dep; unjustified type suppressions; silent solid O(n²) on a common API; deliberate new debt with no recorded decision; public contract change left undocumented *and* undiscussed |
| 🟡 `[important]` | Dishonest cost signal; format parity hole without docs; missing red-green test for a bugfix; threat-model gap touched but unaddressed; CLI forced to import `internal/`; code contorted to match a questionable spec without raising a revision; non-obvious logic that only makes sense after reading OpenSpec/`design.md`/long PR prose (pass-1 doc debt, §8) |
| 🟢 `[nit]` | Naming, comment polish, non-user-facing refactor suggestions |
| 💡 / 📚 / 🎉 | Alternatives, teaching notes, praise — non-blocking |

When unsure whether something is 🔴 vs 🟡: **does it undercut a VISION claim or the
error/safety contract?** If yes → 🔴.

---

## 8. Suggested review order (PR) — code first, then context

This **overrides** the skill’s Phase 1 “absorb the design narrative before the
diff.” For archivey PRs, use two passes with different jobs. Context is still
**required** — it comes second, not never.

> **Scope:** this order is for **code / PR reviews**. Reviewing an OpenSpec proposal,
> delta spec, or `design.md` instead? There is no resulting code tree to read cold —
> use **§9** (values-first), not this order.

### Before either pass (logistics only — ≤1 minute)

Do **not** read the OpenSpec change, design notes, or long PR rationale yet.

- [ ] Scope: `git diff main...HEAD` (or the paths / PR the author named); note size
  (>400 lines? ask to split)
- [ ] CI / local gates red or green (`ruff`, pyrefly/ty, pytest) — enough to know
  whether failures are in-scope
- [ ] Linked artifact **names** only (issue #, `openspec/changes/<name>/`,
  `review/` finding ID) so you know what to open in pass 2 — not the prose yet

### Pass 1 — code alone

Read the changed code (diff + nearby context) **cold**. Ask:

- [ ] Does the **resulting** code make sense **self-contained** — logic, edge cases,
  API shape, safety/streaming/cost without needing external docs?
- [ ] Are **non-obvious** choices explained **in the code** (or an adjacent module
  docstring / comment) — format quirks, hostile-input edges, why this branch /
  sentinel / exception path exists?
- [ ] Would a future editor who only has the tree (not the PR or OpenSpec change)
  understand *why*, not just *what*?
- [ ] Tests: behavior coverage, red–green for fixes (§4); domain checklist rows that
  are visible from the change (§5)

Use the skill’s high-level + line-by-line techniques here
(correctness / security / performance / maintainability / reuse). Skim this
addendum’s §1–§5 only as a **mental checklist**, not by loading linked designs.

**Documentation debt rule:** if a pass-1 concern only dissolves after reading
external prose (OpenSpec `design.md`, long PR body, `docs/decisions/`, …), that is
usually **🟡 `[important]` documentation debt in the code** — not proof you should
have absorbed the design first. A comment that **summarizes *why* inline** and
optionally points at a spec / decision / exploration is fine; a bare “see design.md”
with no local reason is not. Specs and design notes explain *why we chose this
approach*; they are a poor substitute for *why this local path exists*.

### Pass 2 — whole context (do not skip)

Now open the narrative and contracts:

1. PR description + linked issue / full OpenSpec change (proposal, delta specs,
   `design.md`) / `review/` brief or finding.
2. Applicable rows in this addendum (§1 VISION ranking, §3 contracts, §5 domain)
   and authoritative sources at the top of this file when a finding touches them. For a
   contract-moving change, run the **values & contracts consistency check (§9)** — the
   same checklist proposals get, applied to the resulting behavior.
3. Spec ↔ code ↔ docs: match, intentional revision, or **pause-and-ask** (§3) —
   including “self-contained and clear, but disagrees with the capability scenario
   / invents undecided behavior / breaks format parity.”
4. Gates relevant to the change (targeted pytest; three configs before push when
   behavior depends on extras/versions).
5. Write feedback with skill severity labels; put maintainer decisions in questions,
   not silent resolutions.

| Pass | Job | Pass if… |
|------|-----|----------|
| **1. Code alone** | Correctness, clarity, local docs for non-obvious choices | Resulting code is self-explanatory; surprises are explained near the code |
| **2. Context** | Fit to OpenSpec / VISION / threat model / this addendum | Behavior matches (or intentionally revises) contracts; no silent discrepancies |

**Do not treat a solid pass 1 as license to skip pass 2.** A locally clear change can
still undercut a VISION claim, disagree with a scenario, or land unjustified debt.

---

## 9. Reviewing OpenSpec proposals & design docs (not code)

§8's **code-first** ordering is for **actual code / PR reviews**. When the artifact under
review is an OpenSpec **proposal**, delta spec, or `design.md` — not a diff — there is no
"resulting tree" to read cold. Review it against the project's **values and contracts**
instead. (This same check is pass-2 step 2 for code reviews — see §8; for a proposal it
is the *whole* review.)

Finding discipline (§0) applies unchanged: severity × confidence, over-report and label,
pause-and-ask on conflicts rather than silently reconciling.

### Values & contracts consistency check (VISION / CONTRIBUTING)

Run every proposal — and every contract-moving code change — past these:

- [ ] **Uniform interface / no surprises (§2):** one honest interface preserved? Every
  per-format behavior difference expressed as **data** (`None`, enums, documented
  sentinels), never a silent guess?
- [ ] **Safe by default (§1.2):** zip-slip / symlink escape / bombs still require explicit
  opt-out; the design doesn't quietly relax a safety contract.
- [ ] **Memory-safe hostile parsing (§1.3):** pure-Python parse boundaries preserved;
  crafted input yields honest errors, not native-memory corruption.
- [ ] **Damaged input is first-class (§1.4):** recoverable-members + honest-error posture
  preserved; salvage isn't invented where the backlog hasn't committed to it.
- [ ] **Cost honesty & perf budget (§1.5):** cost signals stay truthful; ≤ ~1.3× budget
  acknowledged in bytes/seeks (not vibes) where the design touches hot paths.
- [ ] **Contracts (§3):** zero-dep core, exception-translation model, sync-first, typing
  story, extras layering — none silently broken by the *design*.
- [ ] **Non-goals (§1):** not smuggling in an async public API, `zipfile`/`py7zr`/`rarfile`
  compat shims, quirk-driven architecture, or in-place 7z/RAR modification.
- [ ] **Threat-model gaps (`O*`):** open gaps the proposal touches are addressed in
  substance, not closed by marketing language.

### Proposal-shape checks

- [ ] **Scope right-sized (§3):** does this actually need a spec/change, or is it a
  bugfix/refactor that moves no contract?
- [ ] **Scenarios are falsifiable:** WHEN/THEN reads as testable behavior, not aspiration
  — a reviewer could write the conformance assertion.
- [ ] **Cross-format parity considered:** the parity hot spots in §2 are addressed where
  the change spans backends.
- [ ] **Error / edge / hostile paths specified**, not just the happy path (§4, §5).
- [ ] **Rationale present:** `design.md` records alternatives considered and the *why*,
  per the library schema — not just the *what* (stub OK for trivial deltas).
- [ ] **Docs move together:** if the contract moves, the matching `openspec/specs/` and
  user/decision docs move in the same change (§3).
- [ ] **Pause-and-ask** on conflicts with existing specs / docs / VISION — surface, don't
  silently reconcile (§3).

### Decision gaps & unknown unknowns

The checks above verify what the proposal *says*; this step hunts what it **doesn't**.
Go looking, don't wait for gaps to surface during implementation.

- [ ] **Implementor decision gaps** — read it as if you must implement it tomorrow. What
  would force you to *guess*? Under-specified error behavior, ambiguous field meaning,
  unhandled format/edge combinations, boundary/empty/overflow values, ordering,
  defaults, concurrency. List each as an explicit question the proposal should **decide
  before coding**, not during.
- [ ] **Unknown unknowns** — what is the proposal not thinking about? Format quirks not
  yet considered, interactions with existing capabilities, cross-format parity fallout
  (§2 hot spots), perf/cost surprises, security edges, dependency/version assumptions.
  Name what we *don't yet know* that could change the design, and how to shrink the
  unknown (spike, oracle comparison against `archivey-dev`/`py7zr`/`rarfile`, corpus
  probe, or a maintainer decision).
- [ ] **Assumptions taken on faith** — for each load-bearing assumption, is it verified
  or assumed? Flag the untested ones and the cheapest way to test them.

These are findings too (§0): a decision gap that could send implementation down the wrong
path is 🟡+ and belongs in **maintainer questions** (pause-and-ask), never a silent
assumption baked into the review.

Rank the same way (§0/§7): a proposal that undercuts a load-bearing VISION claim (§1) is
🔴; a decision gap or thin scenario is 🟡; wording nits are 🟢.

---

## 10. Out of scope for *this* addendum

Generic SOLID, Python footguns, and review etiquette stay in the skill’s existing
docs (`architecture-review-guide.md`, `python.md`, `code-review-best-practices.md`,
…). This file only carries **archivey product/contract standards** distilled from
VISION, CONTRIBUTING, and the `review/` program.
