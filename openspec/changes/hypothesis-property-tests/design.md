## Context

O5's mutation harness (`tests/test_mutation_fuzz.py`) already drives every corpus
archive through open/list/read/extract under bit-flips and truncations, asserting
typed `ArchiveyError` or success. What it does **not** do is explore the
combinatorial space of the pure helpers that sit in front of every backend:

| Helper | Module | Why generative coverage matters |
|--------|--------|----------------------------------|
| `normalize_member_name` | `internal/naming.py` | Meaning-preserving rules over arbitrary Unicode / separators / `..` |
| `resolve_link_target_name` | `internal/naming.py` | Symlink vs hardlink namespaces; absolute / escape → `None` |
| `check_universal` | `internal/filters.py` | Non-bypassable extraction gate; absolute, `..`, null, `.` file, specials |
| `discover_volume_siblings` | `internal/volumes.py` | `.7z.NNN` / `.partN.rar` / `.rNN` patterns + natural order |
| `detect_format` | `internal/detection.py` | Arbitrary prefixes must never raise raw exceptions or hang |

`PLAN.md` / `VISION.md` / threat-model O5 all name Hypothesis for this layer as
the "now" stage; Atheris stays with Phase 6's native parsers.

## Goals / Non-Goals

**Goals:**

- Land Hypothesis in `dev` and a focused property suite that closes O5 item 2.
- Encode the requirement in `testing-contract` so the gate is reviewable.
- Keep examples deterministic (`hypothesis` settings / seeded profiles) so CI is
  reproducible; allow deeper local runs via a profile or env knob if useful.

**Non-Goals:**

- Atheris / coverage-guided fuzzing of 7z/RAR headers (Phase 6 with the parsers).
- OSS-Fuzz / `SECURITY.md` (public-release stage).
- Property-testing the full open/extract path (mutation harness already owns that).
- Changing production helper semantics unless a property exposes a real bug.

## Decisions

### 1. `hypothesis` in `dev` only

Add to `[dependency-groups] dev`, not a runtime extra. The `[core-only]` CI leg
uses `--no-dev`, so Hypothesis stays out of the zero-dep install. Mirror how
`py7zr` / `rarfile` already sit in `dev` as oracles.

**Alternative considered:** a dedicated `[fuzz]` extra — rejected; property tests
are part of the everyday suite, not an opt-in tool.

### 2. One module (or small cluster) under `tests/`, not inline in existing files

Prefer `tests/test_properties_*.py` (e.g. `test_properties_naming.py`,
`test_properties_filters.py`, `test_properties_volumes.py`,
`test_properties_detection.py`) so the mutation harness and the property suite
stay separable in CI selection and failure triage.

### 3. Invariants per helper (the properties themselves)

- **`normalize_member_name`:** never raises; result is non-empty; directories end
  with `/` (except `"."`); when `backslash_is_separator=False`, `\\` is preserved
  as a literal; `..` components are retained (rejection is extraction's job).
- **`resolve_link_target_name`:** never raises; absolute symlink → `None`;
  `..`-escape of archive root → `None`; hardlink targets are root-relative.
- **`check_universal`:** for any name containing a null byte, a `..` component,
  an absolute form, or a non-directory name of `""`/`"."`, raises the documented
  `FilterRejectionError` subclass; safe relative names return without error.
- **`discover_volume_siblings`:** given a temp dir populated from a generated
  volume-name set, either returns `None` (not a volume pattern) or a list in
  natural order that includes the probe path; never raises on ordinary paths.
- **`detect_format`:** over arbitrary `bytes` prefixes (bounded size), returns a
  `FormatInfo` or raises only an `ArchiveyError` subclass — never a raw
  `Exception`, never hangs (rely on Hypothesis deadline + existing peek bounds).

Exact `@given` strategies are an implementation detail; the design constraint is
that each property states a **law**, not a golden example.

### 4. CI budget

Default Hypothesis `max_examples` stays modest (library default or a small
profile override) so the three-config push gate does not balloon. A
`HypothesisProfile` named `ci` vs `deep` (env-selected) is acceptable if the
default proves too slow; do not require `ARCHIVEY_FUZZ_MUTATIONS`-scale depth.

### 5. Threat-model sync

After the suite is green, update O5 item 2 from "Still open" to "Landed" with a
pointer to the new tests — same pattern as the mutation-harness note.

## Risks / Trade-offs

- **[Flaky / slow CI]** → Mitigation: bounded `max_examples`, deadline health
  checks, keep strategies away from huge buffers; detection properties use short
  prefixes (detection already peeks a bounded window).
- **[False confidence]** → Mitigation: properties complement, not replace, the
  adversarial corpus and mutation harness; do not drop curated cases.
- **[Finding a real bug mid-change]** → Mitigation: fix in-tree in this change;
  if the fix needs a behavior/spec decision, pause and surface it (CONTRIBUTING
  discrepancy rule).

## Migration Plan

No migration — additive tests + one `dev` dependency. Rollback = revert the
change; production code path unchanged.

## Open Questions

- None blocking. Optional later: whether volume-discovery properties should also
  cover explicit multi-source sequences (probably out of scope — that's
  `resolve_source`, already unit-tested).
