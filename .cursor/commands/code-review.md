# Code review

Review the relevant code with a code review mindset.

## Priorities (Cursor `/code-review` defaults)

Keep these as the primary lens:

1. **Bugs** and correctness errors
2. **Behavioral regressions**
3. **Security / safety** issues
4. **Missing tests** (especially red–green for bug fixes)

**Findings must be the primary focus**, ordered by severity. Do **not** make code
changes unless the user explicitly asks for them.

## Archivey process (best of both)

This repo vendors a fuller review skill under `.claude/skills/code-review-skill/`.
Combine the priorities above with that skill’s process:

1. **Read process rules:** `.claude/skills/code-review-skill/reference/archivey-review-addendum.md`
   — especially **§8 (code first, then context)**, plus VISION ranking, contracts,
   and the domain checklist. Do **not** absorb OpenSpec / design / long PR rationale
   before the cold code pass.
2. **Pass 1 — code alone:** changed code (+ nearby context) for self-explanatory
   sense in the resulting tree, local docs for non-obvious choices, bugs/safety/tests.
   Use `.claude/skills/code-review-skill/SKILL.md` techniques and severity labels;
   open deeper guides under `reference/` only as needed.
3. **Pass 2 — context (required):** PR narrative, OpenSpec change, VISION / threat
   model / addendum rows that apply — check contract fit; pause-and-ask on
   discrepancies. Findings that only dissolve after external prose are usually
   documentation debt in the code (addendum §8).

## Scope

- Default: current branch vs `main` (`git diff main...HEAD` and/or `@Branch`), plus any
  paths or PR the user named.
- If the user is asking about uncommitted work, include the working-tree diff.
- Prefer concrete `file:line` evidence and triggering inputs/states.
- **Reviewing an OpenSpec proposal / `design.md` instead of code?** Skip the code-first
  order and use the addendum's **§9 (values-first)** — check the design against
  VISION/CONTRIBUTING values and contracts, then proposal shape.

## Output format

Lead with findings (no long preamble). Follow **addendum §0 (finding discipline)**:
over-report on existence, label honestly, verification *reclassifies* (a disproven bug
often becomes a clarity/doc-debt finding) — it never silently culls. For each finding:

- **Severity** (impact if real): 🔴 `[blocking]` / 🟡 `[important]` / 🟢 `[nit]` /
  💡 `[suggestion]` / 📚 `[learning]` / 🎉 `[praise]`
- **Confidence** (how well traced): `CONFIRMED` / `PLAUSIBLE` / `DISPROVEN→reclassified`
- Rank archivey blockers using the addendum (VISION claims, exception contract,
  path/bomb safety, silent solid re-decode, unjustified debt, etc.); order by severity,
  then confidence
- Location, what’s wrong, why it matters, and a concrete fix direction
- A concrete trigger where possible (`CONFIRMED` + trigger = red–green regression-test
  candidate); missing trigger lowers confidence, not existence
- Call out missing tests when behavior changed

After findings, a short **Verdict**: Approve / Comment / Request Changes — plus any
maintainer questions (pause-and-ask; do not silently resolve spec conflicts).

Skip formatting/lint nits that `ruff` / type-checkers already own.
