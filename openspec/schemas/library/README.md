# `library` OpenSpec schema

Default Archivey workflow: **proposal → specs + design → tasks**.

Tuned for a developer-facing Python library:

- **Specs** keep OpenSpec’s `### Requirement:` / `#### Scenario:` headers
  (required by `openspec validate`) but prefer signatures + matrices over
  user-story BDD.
- **design.md** holds technical investigations, alternatives, and decisions
  (format parsers, concurrency, codecs, etc.). For trivial deltas, a short
  stub is enough — the file is still required so status stays complete.

## Activate

```yaml
# openspec/config.yaml
schema: library
```

Or per change: `openspec new change <name> --schema library`

## Artifact roles

| Artifact | Role |
| --- | --- |
| `proposal.md` | Why / what / which capabilities |
| `specs/**/*.md` | Normative caller-visible behavior (dense) |
| `design.md` | HOW: investigations, rejected options, module layout |
| `tasks.md` | Checkbox implementation plan |

`specs` and `design` both depend only on `proposal`, so either can be written
first (common for spikes: design before specs crystallize).

## When to use something else

| Schema | Use when |
| --- | --- |
| `library` (default) | Normal and hard library changes |
| `minimalist` | Tiny deltas where proposal + design are overhead |
| `spec-driven` | Prefer the stock four-artifact wording (same shape as `library`) |

## Spec density (summary)

- No user stories; normative `SHALL`/`MUST` only
- Prefer API signatures and markdown tables inside scenarios
- One scenario per behavioral axis; don’t explode edge cases into WHEN/THEN farms
- Don’t restate what type hints already declare
- Put “why we chose X over Y” in design.md, not in requirement prose
