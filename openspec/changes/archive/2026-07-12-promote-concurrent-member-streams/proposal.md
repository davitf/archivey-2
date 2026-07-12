## Why

`MemberStreams.CONCURRENT` shipped provisional in #59 (cooperative guarantee only). The
authoritative specs already require a Linux CPython `3.13t` `free-threaded-concurrency`
job and free-threaded stress coverage, but CI and the heavier multi-thread tests were
deferred. Closing that gap promotes `CONCURRENT` from provisional to supported and
matches the synced `packaging-and-extras` / `testing-contract` requirements.

## What Changes

- Add a required Linux CI job that installs CPython `3.13t` and runs
  `pytest -m concurrent_reader` in the zero-dep core environment.
- Introduce the `concurrent_reader` pytest marker and apply it to directory, ZIP,
  single-file stdlib, SharedSource, lifecycle/operation-state, and TAR concurrent tests
  (optional backends skipped there do not count as free-threaded support).
- Land multi-thread / interleaved stress coverage deferred from
  `concurrent-member-streams` (7.3/7.4/7.6 reminders) and `tar-concurrent-open`
  (4.1/4.2/4.4/4.7/4.8).
- Record a proportionate TAR/ISO lock baseline (wall + wait/hold; no pass/fail threshold)
  — deferred tasks 7.9 / tar §5.1.
- Remove "provisional" wording from public docs/docstrings (`MemberStreams`,
  `project.md`, `SPEC.md`, `ARCHITECTURE.md`, `IDEAS.md`, `docs/grab-bag/parallel-reader.md`,
  threat-model C4) so the supported cooperative + free-threaded-tested seam is stated
  honestly.
- Keep 7z/RAR concurrent-open code compliance deferred until those readers exist
  (design note already recorded).

## Capabilities

### New Capabilities

_(none — this promotes an existing declared capability)_

### Modified Capabilities

- `packaging-and-extras`: affirm the free-threaded correctness contract is exercised by
  the required `3.13t` CI job (no longer deferred / provisional).
- `testing-contract`: require the `concurrent_reader` marker +
  `free-threaded-concurrency` job as landed CI, plus multi-thread stress expectations
  for the promoted seam.
- `format-tar`: multi-thread concurrent-open coverage under `CONCURRENT` (plain and
  compressed TAR-RA) becomes a required test obligation, not a deferred reminder.
- `format-iso`: multi-thread concurrent-open coverage under `CONCURRENT` becomes a
  required test obligation, not a deferred reminder.

## Impact

- `.github/workflows/ci.yml` gains a Linux `3.13t` job (core-only + marked tests).
- New/extended tests under `tests/` (markers + multi-thread stress); possible small
  helpers in `tests/conftest.py`.
- Public/prose docs lose "provisional" language; threat-model C4 stays aligned with the
  tested free-threaded claim.
- Optional: a non-gating baseline script/notes under `benchmarks/` or `docs/` for TAR/ISO
  lock cost (no CI speed gate).
- No public API shape change; `MemberStreams.CONCURRENT` remains opt-in.
