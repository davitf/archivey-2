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

1. **Read first:** `.claude/skills/code-review-skill/reference/archivey-review-addendum.md`
   (VISION ranking, zero tech debt, specs-as-guidelines, domain checklist)
2. **Then follow:** `.claude/skills/code-review-skill/SKILL.md`
   (four-phase review, severity labels, constructive feedback norms)
3. Open deeper guides under `.claude/skills/code-review-skill/reference/` only as needed
   (security, performance, Python, error handling, architecture, quality)

## Scope

- Default: current branch vs `main` (`git diff main...HEAD` and/or `@Branch`), plus any
  paths or PR the user named.
- If the user is asking about uncommitted work, include the working-tree diff.
- Prefer concrete `file:line` evidence and triggering inputs/states.

## Output format

Lead with findings (no long preamble). For each finding:

- Severity from the skill: 🔴 `[blocking]` / 🟡 `[important]` / 🟢 `[nit]` /
  💡 `[suggestion]` / 📚 `[learning]` / 🎉 `[praise]`
- Rank archivey blockers using the addendum (VISION claims, exception contract,
  path/bomb safety, silent solid re-decode, unjustified debt, etc.)
- Location, what’s wrong, why it matters, and a concrete fix direction
- Call out missing tests when behavior changed

After findings, a short **Verdict**: Approve / Comment / Request Changes — plus any
maintainer questions (pause-and-ask; do not silently resolve spec conflicts).

Skip formatting/lint nits that `ruff` / type-checkers already own.
