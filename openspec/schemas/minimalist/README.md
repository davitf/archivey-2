# Minimalist OpenSpec Schema

`minimalist` is **specs → tasks** for tiny, low-risk library deltas.

It is **not** a different spec grammar. Deltas must still use
`## ADDED Requirements` / `### Requirement:` / `#### Scenario:` so
`openspec validate` passes. Bodies should stay compact (signatures + matrices).

> Older community Minimalist templates used user stories + Given/When/Then.
> Those **fail** `openspec validate` here — do not use that format.

## Activate

```yaml
# openspec/config.yaml — project default is `library`, not this
schema: library
```

Per change:

```bash
openspec new change <name> --schema minimalist
```

## When to use

| Schema | Fit |
| --- | --- |
| `library` (default) | Normal/hard changes — proposal + compact specs + design + tasks |
| `minimalist` | Trivial deltas where proposal and design are overhead |
| `spec-driven` | Stock wording; same four-artifact shape as `library` |

## Spec format

Same as `library`: Requirement/Scenario headers, dense bodies, no user stories.
