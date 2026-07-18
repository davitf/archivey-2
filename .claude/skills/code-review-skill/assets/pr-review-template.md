# PR Review Template

Copy and use this template for code reviews in this repo.

---

## Summary

[Brief overview of what was reviewed — 1-2 sentences]

**PR Size:** [Small/Medium/Large] (~X lines)
**Review Time:** [X minutes]

## Strengths

- [What was done well]
- [Good patterns or approaches used]
- [Improvements from previous code]

## Architecture & Performance

**Architecture Assessment**
- [ ] Separation of concerns — responsibilities clearly divided?
- [ ] Fits existing reader/backend patterns?
- [ ] Dependency direction toward stable abstractions?
- [ ] Public API / exception contract preserved or intentionally changed?

> See [Architecture Review Guide](../reference/architecture-review-guide.md).

**Performance Assessment**
- [ ] Algorithm / member-loop complexity acceptable?
- [ ] Memory — streaming vs full buffers; unbounded growth?
- [ ] I/O — avoid silent re-decompression / redundant reads?

> See [Performance Review Guide](../reference/performance-review-guide.md).

## Required Changes

🔴 **[blocking]** [Issue description]
> [Code location or example]
> [Suggested fix or explanation]

## Important Suggestions

🟡 **[important]** [Issue description]
> [Why this matters]
> [Suggested approach]

## Minor Suggestions

🟢 **[nit]** [Minor improvement suggestion]

💡 **[suggestion]** [Alternative approach to consider]

## Learning Notes

📚 [Educational context]

## Security Considerations

- [ ] No hardcoded secrets
- [ ] Extract path / symlink safety considered
- [ ] Hostile size / bomb risks considered
- [ ] Subprocess usage is safe (no shell interpolation)
- [ ] Passwords / key material not leaked in logs/errors
- [ ] Dependency / extra changes justified for zero-dep core

> See [Security Review Guide](../reference/security-review-guide.md).

## Test Coverage

- [ ] Unit / behavior tests added or updated
- [ ] Edge, truncated, and error cases covered
- [ ] Format fixtures / corpus used where appropriate

## Verdict

**[ ] ✅ Approve** — Ready to merge
**[ ] 💬 Comment** — Minor suggestions, can merge
**[ ] 🔄 Request Changes** — Must address blocking issues

---

## Quick Copy Templates

### Blocking Issue
```
🔴 **[blocking]** [Title]

[Description of the issue]

**Location:** `path/to/file.py:123`

**Suggested fix:**
\`\`\`python
# suggested code
\`\`\`
```

### Important Suggestion
```
🟡 **[important]** [Title]

[Why this is important]

**Consider:**
- Option A: [description]
- Option B: [description]
```

### Minor Suggestion
```
🟢 **[nit]** [Suggestion]

Not blocking, but consider [improvement].
```

### Praise
```
🎉 **[praise]** Great work on [specific thing]!

[Why this is good]
```

### Learning
```
📚 **[learning]** [Educational note]

For context, [X] works this way because [Y]. No action needed — just sharing.
```
