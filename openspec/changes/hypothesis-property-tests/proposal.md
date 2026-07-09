## Why

Threat-model O5 and `PLAN.md` treat Hypothesis property tests for the pure safety
logic as a **Phase 6 entry gate** (alongside the already-landed mutation harness).
Native 7z/RAR parsers will ingest untrusted binary headers in Python; before that
surface lands, the deterministic safety helpers that every backend already shares —
name normalization, universal path checks, link-target resolution, volume discovery,
detection over arbitrary prefixes — need generative coverage that curated fixtures
and bit-flip mutation cannot give. Why now: Phase 5 is archived, Phase 6 has no
change yet, and this gate is the cheap half of the remaining O5 work (Atheris stays
with the native parsers themselves).

## What Changes

- Add `hypothesis` to the `dev` dependency group.
- Add property-based tests over the pure helpers:
  - `normalize_member_name` / `resolve_link_target_name` (`internal/naming.py`)
  - `check_universal` (`internal/filters.py`)
  - `discover_volume_siblings` (`internal/volumes.py`)
  - `detect_format` over arbitrary byte prefixes (never raw exception / hang)
- Extend `testing-contract` with an explicit property-based-testing requirement so
  the gate is specced, not just tribal knowledge in the threat model.
- Document the O5 progress update in `docs/threat-model.md` once the suite lands
  (implementation task; no new capability).

## Capabilities

### New Capabilities

_(none)_

### Modified Capabilities

- `testing-contract`: add a requirement that the pure safety / detection helpers are
  covered by Hypothesis property tests with the documented invariants (never crash,
  never hang, typed errors or success; normalization and path-safety laws).

## Impact

- **Deps:** `hypothesis` in `[dependency-groups] dev` only — zero-dep core and
  runtime extras unchanged; the `[core-only]` CI leg stays free of it.
- **Code:** no production code changes expected (tests + threat-model note only),
  unless a property finds a real bug — then a focused fix lands in the same change.
- **CI:** property tests run in the everyday / `[all]` / `[all-lowest]` legs with
  the rest of the suite; they skip or are absent under `--no-dev`.
- **Does not:** stand up Atheris, OSS-Fuzz, or `SECURITY.md` (later O5 stages);
  does not block or implement native 7z/RAR.
