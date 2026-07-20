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
   and authoritative sources at the top of this file when a finding touches them.
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

## 9. Out of scope for *this* addendum

Generic SOLID, Python footguns, and review etiquette stay in the skill’s existing
docs (`architecture-review-guide.md`, `python.md`, `code-review-best-practices.md`,
…). This file only carries **archivey product/contract standards** distilled from
VISION, CONTRIBUTING, and the `review/` program.
