# Archivey Review Addendum

> **This is a repo-specific focus doc layered on top of the generic code-review skill.**
> Use the skill’s process, severity labels, and Python/quality guides as the base;
> use **this file** for what archivey uniquely cares about.
>
> Do not merge these rules into the upstream-derived guides — keep the delta visible.

**Authoritative sources (read these when a finding touches them):**

| Source | Role |
|--------|------|
| [`VISION.md`](../../../../VISION.md) | Product tie-breaker when trade-offs conflict |
| [`CONTRIBUTING.md`](../../../../CONTRIBUTING.md) | Coding, typing, exceptions, testing, three-config gate |
| [`openspec/specs/`](../../../../openspec/specs/) | Normative behavior contracts |
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

### Clean-as-you-go

- [ ] Touched code is left in the shape it *should* have (rename/move/doc/spec sync in
  the same change when the contract moves)
- [ ] **Pause and ask** on spec↔doc↔code discrepancies — do not silently pick a winner
  (`CONTRIBUTING.md`, `CLAUDE.md`, `review/README.md`)

### Comments

- [ ] Explain *why* (format quirks, hostile-input edges), not narrate *what*
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

### Specs & docs

- [ ] Behavior changes update the relevant `openspec/specs/` (or an `openspec/changes/`
  proposal) and user/decision docs in the **same** change
- [ ] Open threat-model gaps (`O*`) are not “fixed” by marketing language alone

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
5. **Pause and ask** — spec/design conflicts go to `QUESTIONS.md`, not silent fixes.
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
| 🔴 `[blocking]` | Path escape on default extract; catch-all exception translation; core grows a hard dep; unjustified type suppressions; silent solid O(n²) on a common API; public contract change without spec/decision |
| 🟡 `[important]` | Dishonest cost signal; format parity hole without docs; missing red-green test for a bugfix; threat-model gap touched but unaddressed; CLI forced to import `internal/` |
| 🟢 `[nit]` | Naming, comment polish, non-user-facing refactor suggestions |
| 💡 / 📚 / 🎉 | Alternatives, teaching notes, praise — non-blocking |

When unsure whether something is 🔴 vs 🟡: **does it undercut a VISION claim or the
error/safety contract?** If yes → 🔴.

---

## 8. Suggested review order (PR)

1. Read PR description + linked issue / OpenSpec change / `review/` finding ID.
2. Skim this addendum’s domain checklist (§5) for applicable rows.
3. Run the skill’s four-phase process; pull generic Python/quality/security guides
   only as needed.
4. Verify gates relevant to the change (`ruff`, pyrefly/ty, targeted pytest; three
   configs before push when behavior depends on extras/versions).
5. Write feedback with skill severity labels; put maintainer decisions in questions,
   not silent resolutions.

---

## 9. Out of scope for *this* addendum

Generic SOLID, Python footguns, and review etiquette stay in the skill’s existing
docs (`architecture-review-guide.md`, `python.md`, `code-review-best-practices.md`,
…). This file only carries **archivey product/contract standards** distilled from
VISION, CONTRIBUTING, and the `review/` program.
