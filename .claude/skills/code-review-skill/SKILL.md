---
name: code-review-skill
description: |
  Provides comprehensive code review guidance for Python, plus an archivey-specific
  addendum (VISION, CONTRIBUTING, review/ standards). Wired from Cursor `/code-review`
  via `.cursor/commands/code-review.md`.
  Covers architecture review, performance review, security audit, code quality anti-patterns,
  and common bugs.
  Use when: reviewing pull requests, conducting PR reviews, code review, reviewing code changes,
    establishing review standards, mentoring developers, architecture reviews, security audits,
    performance reviews, checking code quality, finding bugs, giving feedback on code,
    or when the user invokes /code-review.
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash      # run lint/test/build commands to verify code quality
  - WebFetch  # look up current docs and best practices
---

# Code Review Skill

Transform code reviews from gatekeeping to knowledge sharing through constructive feedback, systematic analysis, and collaborative improvement.

> **Repo note:** This is a Python-only, English subset of
> [awesome-skills/code-review-skill](https://github.com/awesome-skills/code-review-skill)
> (MIT), trimmed for **archivey** (pure Python archive library). Non-Python guides,
> web/SQL/ORM/CSS material, and upstream Simplified Chinese prose were removed or
> rewritten.
>
> **Archivey-specific standards** live in a separate addendum (do not fold them into
> the generic guides):
> **[reference/archivey-review-addendum.md](reference/archivey-review-addendum.md)** —
> VISION tie-breakers, CONTRIBUTING contracts, and `review/` deep-review norms.
> **Read that addendum first** when reviewing changes in this repository.
>
> **Cursor entrypoint:** project command [`.cursor/commands/code-review.md`](../../.cursor/commands/code-review.md)
> wires `/code-review` to this skill + addendum (findings-first defaults + archivey
> process). Invoking `/code-review-skill` also loads this skill directly.
>
> **Repo default — findings first, no edits:** in archivey, `/code-review` and
> `/code-review-skill` **report findings**; they do **not** modify code unless you
> explicitly ask. Lead with findings ranked by **severity × confidence** (see
> [addendum §0](reference/archivey-review-addendum.md)); output is markdown prose, not a
> host-specific findings tool. This holds however the skill is invoked (Cursor command or
> `Skill`). Reviewing an OpenSpec proposal rather than code? Use
> [addendum §9](reference/archivey-review-addendum.md) (values-first), not the code-first
> order.

## When to Use This Skill

- Reviewing pull requests and code changes
- Establishing code review standards for teams
- Mentoring junior developers through reviews
- Conducting architecture reviews
- Creating review checklists and guidelines
- Improving team collaboration
- Reducing code review cycle time
- Maintaining code quality standards

## Core Principles

### 1. The Review Mindset

**Goals of Code Review:**
- Catch bugs and edge cases
- Ensure code maintainability
- Share knowledge across team
- Enforce coding standards
- Improve design and architecture
- Build team culture

**Not the Goals:**
- Show off knowledge
- Nitpick formatting (use linters)
- Block progress unnecessarily
- Rewrite to your preference

### 2. Effective Feedback

**Good Feedback is:**
- Specific and actionable
- Educational, not judgmental
- Focused on the code, not the person
- Balanced (praise good work too)
- Prioritized (critical vs nice-to-have)

```markdown
❌ Bad: "This is wrong."
✅ Good: "This could cause a race condition when multiple users
         access simultaneously. Consider using a mutex here."

❌ Bad: "Why didn't you use X pattern?"
✅ Good: "Have you considered the Repository pattern? It would
         make this easier to test. Here's an example: [link]"

❌ Bad: "Rename this variable."
✅ Good: "[nit] Consider `userCount` instead of `uc` for
         clarity. Not blocking if you prefer to keep it."
```

### 3. Review Scope

**What to Review:**
- Logic correctness and edge cases
- Security vulnerabilities
- Performance implications
- Test coverage and quality
- Error handling
- Documentation and comments
- API design and naming
- Architectural fit

**What Not to Review Manually:**
- Code formatting (use Prettier, Black, etc.)
- Import organization
- Linting violations
- Simple typos

## Review Process

### Phase 1: Context Gathering (2-3 minutes)

> **Archivey override:** For PRs in this repo, **do not** use the generic “read the
> design narrative before the diff” Phase 1. Follow the addendum’s **§8 (code first,
> then context)** instead. Keep only logistics before the cold code pass; full
> narrative context is pass 2 (required — do not skip).

**Logistics only** (≤1 minute — no OpenSpec / design / long PR rationale yet):

1. Scope: `git diff main...HEAD` (or the paths / PR the author named); note size
   (>400 lines? Ask to split)
2. CI / local gates (`ruff`, pyrefly/ty, pytest) — enough to know whether failures
   are in-scope
3. Linked artifact **names** only (issue #, `openspec/changes/<name>/`, `review/`
   finding ID) so you know what to open in pass 2 — not the prose yet
4. Skim **[Archivey Review Addendum](reference/archivey-review-addendum.md) §8**
   for the two-pass process (§1–§5 as a mental checklist only — do not load linked
   designs yet)

Then run Phases 2–3 as **pass 1 (code alone)**. Open the PR narrative, OpenSpec
change, and contracts as **pass 2** per addendum §8 before writing the verdict.

> For large diffs, pipe the diff through [`scripts/pr-analyzer.py`](scripts/pr-analyzer.py) (`git diff main...HEAD | python scripts/pr-analyzer.py`) to triage complexity and get a suggested review approach before reading.

### Phase 2: High-Level Review (5-10 minutes)

1. **Architecture & Design** - Does the solution fit the problem?
   - For significant changes, consult [Architecture Review Guide](reference/architecture-review-guide.md)
   - Check: SOLID principles, coupling/cohesion, anti-patterns
2. **Performance Assessment** - Are there performance concerns?
   - For performance-critical code, consult [Performance Review Guide](reference/performance-review-guide.md)
   - Check: Algorithm complexity, streaming vs buffering, silent re-decompression, memory usage
3. **File Organization** - Are new files in the right places?
4. **Testing Strategy** - Are there tests covering edge cases?

### Phase 3: Line-by-Line Review (10-20 minutes)

For each file, check:
- **Logic & Correctness** - Edge cases, off-by-one, None checks, hostile/truncated input
- **Security** - Path traversal, zip bombs, subprocess safety, secrets
- **Performance** - Redundant decompression, unnecessary loops, unbounded buffers
- **Maintainability** - Clear names, single responsibility, comments
- **Reuse** - Before accepting new code, search for existing utilities/helpers that could replace it. Check adjacent files and shared modules for similar patterns. See [Universal Quality Guide](reference/code-quality-universal.md) for anti-patterns like parameter sprawl, leaky abstractions, nested conditionals, stringly-typed code, TOCTOU, and no-op updates.

### Phase 4: Summary & Decision (2-3 minutes)

1. Summarize key concerns
2. Highlight what you liked
3. Make clear decision:
   - ✅ Approve
   - 💬 Comment (minor suggestions)
   - 🔄 Request Changes (must address)
4. Offer to pair if complex

## Review Techniques

### Technique 1: The Checklist Method

Use checklists for consistent reviews. See [Security Review Guide](reference/security-review-guide.md) for comprehensive security checklist.

### Technique 2: The Question Approach

Instead of stating problems, ask questions:

```markdown
❌ "This will fail if the list is empty."
✅ "What happens if `items` is an empty array?"

❌ "You need error handling here."
✅ "How should this behave if the API call fails?"
```

### Technique 3: Suggest, Don't Command

Use collaborative language:

```markdown
❌ "You must change this to use async/await"
✅ "Suggestion: async/await might make this more readable. What do you think?"

❌ "Extract this into a function"
✅ "This logic appears in 3 places. Would it make sense to extract it?"
```

### Technique 4: Differentiate Severity

Use labels to indicate priority:

- 🔴 `[blocking]` - Must fix before merge
- 🟡 `[important]` - Should fix, discuss if disagree
- 🟢 `[nit]` - Nice to have, not blocking
- 💡 `[suggestion]` - Alternative approach to consider
- 📚 `[learning]` - Educational comment, no action needed
- 🎉 `[praise]` - Good work, keep it up!

**Severity levels:** 🔴 / 🟡 / 🟢 are the three severity tiers used as the standard across all guides in this skill — 🔴 blocks the merge, 🟡 should be addressed, 🟢 is optional. The remaining markers (💡 / 📚 / 🎉) are non-blocking annotations.

## Language-Specific Guides

This install keeps only the Python guide (other languages omitted):

| Language/Framework | Reference File | Key Topics |
|-------------------|----------------|------------|
| **Python** | [Python Guide](reference/python.md) | Mutable default args, exception handling, class attributes |

## Archivey focus (addendum)

| Doc | Role |
|-----|------|
| **[Archivey Review Addendum](reference/archivey-review-addendum.md)** | Repo-specific standards on top of this skill: VISION ranking, exception/zero-dep/typing contracts, testing, safety/streaming/API checklist, `review/` deep-review shape |

## Cross-Cutting Guides

Guides scoped to this Python archive library (web/SQL/ORM/CSS material removed):

| Topic | Reference File | Key Topics |
|-------|----------------|------------|
| **Architecture Review** | [Architecture Review Guide](reference/architecture-review-guide.md) | SOLID, anti-patterns, coupling/cohesion, dependency direction |
| **Performance Review** | [Performance Review Guide](reference/performance-review-guide.md) | Streaming, solid-archive costs, memory, algorithmic complexity |
| **Security Review** | [Security Review Guide](reference/security-review-guide.md) | Path traversal, zip bombs, hostile parsers, subprocess, secrets |
| **Universal Quality** | [Universal Quality Guide](reference/code-quality-universal.md) | Reuse audit, parameter sprawl, leaky abstractions, nested conditionals, stringly-typed code, TOCTOU |
| **Common Bugs** | [Common Bugs Checklist](reference/common-bugs-checklist.md) | Python + archive-library bug patterns |
| **Error Handling** | [Error Handling Guide](reference/cross-cutting/error-handling-principles.md) | Fail fast, exception hierarchy/translation, logging |
| **Async & Concurrency** | [Concurrency Guide](reference/cross-cutting/async-concurrency-patterns.md) | Python races/deadlocks; sync-first API note |
| **Review Best Practices** | [Code Review Best Practices](reference/code-review-best-practices.md) | Communication, reviewer mindset, severity labels |

## Additional Resources

- [PR Review Template](assets/pr-review-template.md) - PR review comment template
- [Review Checklist](assets/review-checklist.md) - Quick reference checklist
