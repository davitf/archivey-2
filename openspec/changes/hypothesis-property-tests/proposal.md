# Hypothesis property tests for the pure safety logic (Phase 6 fuzzing gate)

## Why

`docs/threat-model.md` O5 defines the fuzzing scaffold that must be standing before the
native 7z/RAR parsers ship (they parse untrusted binary headers in Python). O5 has three
staged parts:

1. **Landed** — the corpus **mutation harness** (`tests/test_mutation_fuzz.py`, PR #47):
   every corpus archive is deterministically mutated and driven through
   open/list/read/extract + detection, asserting *typed `ArchiveyError` or success, never a
   raw exception, never a hang*.
2. **Open (this change)** — **property-based tests (Hypothesis)** for the load-bearing
   safety logic: `normalize_member_name`, `check_universal`, `resolve_link_target_name`,
   volume discovery, and format detection over arbitrary byte prefixes. Their invariants are
   expressible as properties — exactly what Hypothesis is good at, and where the curated
   example tests can only cover the cases we already thought of.

   **Not all five are I/O-free** — that matters for how they are tested:
   - Genuinely pure (string/parse only): `normalize_member_name`, `resolve_link_target_name`,
     and the name-parsing parts of volume discovery (part-number regexes). Tested as pure
     properties over generated strings.
   - Touch the filesystem: `check_universal` calls `Path.resolve()` on the dest / parents (so
     real symlinks matter), and `discover_volume_siblings` calls `is_file()` / `iterdir()`.
     These are tested with `tmp_path`-rooted strategies that materialize the relevant tree
     (including symlink layouts), **not** as pure functions. The proposal deliberately drops
     any "pure, no I/O" claim over the whole set.
3. **Deferred to Phase 6 itself** — coverage-guided Atheris fuzzing of the native header
   parsers lands *with* those parsers (see the parser changes), not here.

This change closes part 2, which is one of the two named pre-Phase-6 entry gates in
`PLAN.md` (the other is the shared-source stream plumbing).

## What Changes

- Add `hypothesis` to the **`dev` dependency group** (test-only; the runtime core stays
  zero-dependency).
- Add `tests/test_property_safety.py` with Hypothesis strategies + property tests for the
  pure safety functions. Each test asserts an **invariant**, not a golden value — e.g.
  "`check_universal` never returns normally for a name containing a `..` component,"
  "`normalize_member_name` output never introduces a `..` that the input lacked,"
  "detection over an arbitrary byte prefix never raises and never consumes the stream."
- Run **inline in the normal pytest suite**: a bounded, deterministic example budget on
  every CI job (a fixed `derandomize`/seed profile so failures are reproducible), with a
  deeper sweep gated behind an env var (`ARCHIVEY_FUZZ_EXAMPLES`, mirroring the mutation
  harness's `ARCHIVEY_FUZZ_MUTATIONS`). No new CI job.
- A **shrink-repro discipline**: any Hypothesis-found counterexample is added as an explicit
  regression case (Hypothesis `@example` or a plain unit test) so it is pinned even if the
  strategy later drifts.
- Extend `testing-contract` with a requirement covering the property-test layer.

**Non-goals.** No Atheris / coverage-guided fuzzing (Phase 6, with the parsers). No fuzzing
of the accelerator C-extensions (`docs/threat-model.md` O5 keeps that in a resource-limited
subprocess sandbox, deferred). No behavior change to any runtime code — this is test-only,
though a counterexample that reveals a real bug is fixed as part of the change (as the
mutation harness did).

## Impact

- Affected specs: `testing-contract` (ADDED requirement).
- Affected code: `pyproject.toml` (`dev` group), `tests/test_property_safety.py` (new), plus
  any safety-logic fix a counterexample surfaces.
- Risk: low — test-only, always-on but bounded so CI time stays predictable.
- Unblocks: the Phase 6 native-reader entry gate (fuzzing scaffold complete once this + the
  landed mutation harness are both green).
