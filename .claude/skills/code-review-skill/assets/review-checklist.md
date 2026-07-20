# Code Review Quick Checklist

Quick reference for reviewing changes in this Python archive library.

Archivey order: **code first, then context** (addendum §8). Do not absorb OpenSpec /
design / long PR rationale before the cold code pass.

## Logistics (≤1 min) — before either pass

- [ ] Scope: diff vs `main` (or named paths); size (<400 lines ideal, else ask to split)
- [ ] CI / local gates (`ruff`, pyrefly/ty, pytest)
- [ ] Linked artifact **names** only (issue #, `openspec/changes/<name>/`, `review/` ID)
  — not the prose yet

## Pass 1 — code alone

Read the changed code (+ nearby context) cold. Self-explanatory resulting tree;
local *why* for non-obvious choices; bugs / safety / tests.

### Architecture & Design (5 min)

- [ ] Solution fits the problem
- [ ] Consistent with existing backends / patterns
- [ ] No simpler approach exists
- [ ] Public API impact is intentional and documented
- [ ] Changes land in the right module layer

### Logic & Correctness (10 min)

- [ ] Edge cases / truncated / hostile input handled
- [ ] `None` / optional metadata handled
- [ ] Off-by-one / length-field checks
- [ ] Error handling uses the library exception contract
- [ ] Format parity preserved (or differences are explicit data)

### Security (5 min)

- [ ] No hardcoded secrets
- [ ] Path traversal / symlink escape considered on extract paths
- [ ] Resource limits / bomb risks considered
- [ ] Subprocess args are lists (no `shell=True` footguns)
- [ ] Passwords / key material not logged

### Performance (3 min)

- [ ] No silent re-decompression / O(n²) member loops
- [ ] Streaming preferred over full buffering where appropriate
- [ ] Handles closed; no unbounded buffers
- [ ] Hot-path copies justified

### Testing (5 min)

- [ ] Tests exist for new behavior
- [ ] Edge / error / hostile cases covered
- [ ] Tests are readable and deterministic
- [ ] Fixtures reused when possible

### Code Quality (3 min)

- [ ] Clear names
- [ ] No unnecessary duplication
- [ ] Functions do one thing
- [ ] Complex parser logic explained where needed
- [ ] No magic numbers (use named constants)

### Documentation (2 min)

- [ ] Public APIs documented
- [ ] Specs / ADRs updated if behavior contracts change
- [ ] Breaking changes called out
- [ ] Resulting code is self-explanatory; non-obvious *why* is near the code (not only
  in OpenSpec / PR prose)

## Pass 2 — context (required)

- [ ] PR description + linked issue / full OpenSpec change / `review/` finding
- [ ] Contract fit: OpenSpec / VISION / threat model / addendum (§1, §3, §5)
- [ ] Spec ↔ code ↔ docs: match, intentional revision, or pause-and-ask
- [ ] Concerns that only dissolve after external prose → usually 🟡 doc debt in the code

---

## Severity Labels

| Label | Meaning | Action |
|-------|---------|--------|
| 🔴 `[blocking]` | Must fix | Block merge |
| 🟡 `[important]` | Should fix | Discuss if disagree |
| 🟢 `[nit]` | Nice to have | Non-blocking |
| 💡 `[suggestion]` | Alternative | Consider |
| 📚 `[learning]` | Educational | No action needed |
| 🎉 `[praise]` | Good work | Celebrate |

---

## Red Flags

- Empty `except:` / swallowed errors
- `shell=True` with path interpolation
- Ad-hoc path joins on extract destinations
- TODO left in production paths without issue link
- Commented-out code
- Magic numbers in parsers
- Copy-pasted codec/backend blocks that should share helpers
- Hardcoded credentials
