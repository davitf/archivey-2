# Code Review Quick Checklist

Quick reference for reviewing changes in this Python archive library.

## Pre-Review (2 min)

- [ ] Read PR description and linked issue / OpenSpec change
- [ ] Check PR size (<400 lines ideal)
- [ ] Verify CI status (tests / ruff / type-check)
- [ ] Understand the behavior under change (format? safety? API?)

## Architecture & Design (5 min)

- [ ] Solution fits the problem
- [ ] Consistent with existing backends / patterns
- [ ] No simpler approach exists
- [ ] Public API impact is intentional and documented
- [ ] Changes land in the right module layer

## Logic & Correctness (10 min)

- [ ] Edge cases / truncated / hostile input handled
- [ ] `None` / optional metadata handled
- [ ] Off-by-one / length-field checks
- [ ] Error handling uses the library exception contract
- [ ] Format parity preserved (or differences are explicit data)

## Security (5 min)

- [ ] No hardcoded secrets
- [ ] Path traversal / symlink escape considered on extract paths
- [ ] Resource limits / bomb risks considered
- [ ] Subprocess args are lists (no `shell=True` footguns)
- [ ] Passwords / key material not logged

## Performance (3 min)

- [ ] No silent re-decompression / O(n²) member loops
- [ ] Streaming preferred over full buffering where appropriate
- [ ] Handles closed; no unbounded buffers
- [ ] Hot-path copies justified

## Testing (5 min)

- [ ] Tests exist for new behavior
- [ ] Edge / error / hostile cases covered
- [ ] Tests are readable and deterministic
- [ ] Fixtures reused when possible

## Code Quality (3 min)

- [ ] Clear names
- [ ] No unnecessary duplication
- [ ] Functions do one thing
- [ ] Complex parser logic explained where needed
- [ ] No magic numbers (use named constants)

## Documentation (2 min)

- [ ] Public APIs documented
- [ ] Specs / ADRs updated if behavior contracts change
- [ ] Breaking changes called out

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
