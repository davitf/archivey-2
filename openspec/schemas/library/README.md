# `library` OpenSpec schema

Default Archivey workflow: **proposal → specs → tasks**.

Tuned for a developer-facing Python library: keep OpenSpec’s
`### Requirement:` / `#### Scenario:` headers (required by `openspec validate`),
but author dense bodies (signatures + matrices) instead of user-story BDD.

## Activate

```yaml
# openspec/config.yaml
schema: library
```

Or per change: `openspec new change <name> --schema library`

## When to use something else

| Schema | Use when |
| --- | --- |
| `library` (default) | Most changes — behavior deltas + tasks |
| `spec-driven` | Cross-cutting / architectural work that needs `design.md` before tasks |
| `minimalist` | Tiny, low-risk deltas where even a proposal is overhead (still validate-compatible) |

## Spec density (summary)

- No user stories; normative `SHALL`/`MUST` only
- Prefer API signatures and markdown tables inside scenarios
- One scenario per behavioral axis; don’t explode edge cases into WHEN/THEN farms
- Don’t restate what type hints already declare
