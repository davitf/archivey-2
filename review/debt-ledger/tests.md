# Test-strategy holes — measured against the bug classes prior reviews found

Original refs: `main` @ `7bb862b`. **Status refresh 2026-07-24** @ `bdf5ffd`.

## First, what got fixed since the old `tests.md` (credit)

- Randomized seek (XZ), both-idioms truncation (`.Z`), BaseException-mid-
  materialization, atomic-replace negative, Atheris gate.
- **T1 solid-RAR mutation** — **DONE in #184** (`_SOLID_RAR_*` +
  `test_solid_rar_mutations_fail_typed_or_succeed`).

## T1 — solid-RAR demux mutation — **DONE (#184)**

Static solid RAR4/RAR5 fixtures under the mutation net. Declarative CORPUS
still does not emit `-s`; encrypted-header / multi-volume static fixtures
remain outside mutation — folded into **T7**.

## T2 — seek-interleaving stops at XZ (PAY — cheap)

Parametrize `test_xz_seek_interleaving_matches_plaintext` over lzip / `.Z`.
**Still open.**

## T3 — benchmark-gate RAR / encrypted / accelerator data cases (PAY)

Still no RAR, encrypted, or accelerator *data* cases in
`test_benchmark_gate.py`. Perf P6 remainder. **Still open.**

## T4 — free-threaded core-only; `*_if_available` untested under threads

- Core-only 3.13t job: **KEEP** (honestly scoped).
- Multithread `members_report_if_available` barrier: **PAY** — still missing.

## T5 / T6 — fault injection leftovers; no stateful concurrency stress (KEEP)

Recorded; pay opportunistically / past 0.2.0 unless parallel extraction lands.

## T7 — corpus matrix thin spots (AUDIT — half-day)

ISO only in `basic`; encrypted-header 7z / multi-volume mainly outside
sweep+mutation. **PAY** audit + cheap extensions; record deliberate exclusions.

## What is actually fine

Layered fuzz story, three-dep CI configs, declarative corpus + sweep, adversarial
name / ZipCrypto / accelerator-shutdown / EOF-probe coverage.
