# Test-strategy holes — measured against the bug classes prior reviews found

The lens (backlog Topic 4): three security reviews repeatedly concluded "no
test in the suite catches this". The question is not line coverage — it is
whether the suite's *generative* nets (corpus sweep, mutation fuzz, Atheris,
Hypothesis) reach the code where review-found bugs actually lived. Original
refs: `main` @ `7bb862b`. **Status refresh 2026-07-23** @ `8cc3ea5`.

## First, what got fixed since the old `tests.md` (credit)

- **F5/T4 randomized seek** — landed for XZ:
  `test_seekable_streams.py` Hypothesis interleaving test (size-probe/seek/read
  vs plaintext oracle). This is exactly the test shape whose absence hid the
  stream-decoder F1 crash.
- **F4 both-idioms truncation** — landed for `.Z` in `test_codecs.py`.
- **T2 BaseException-mid-materialization** — structural + matching test.
- **T3 atomic-replace negative test** — partially landed in `test_extraction.py`.
- **T5 Atheris gate** — landed and extended (7z/RAR headers, open+members,
  detection, ZIP/TAR/ISO, standalone codecs).
- **T1 solid-RAR mutation** — **DONE in #184**: curated static solid RAR4/RAR5
  fixtures under `test_mutation_fuzz.py` (`_SOLID_RAR_*` +
  `test_solid_rar_mutations_fail_typed_or_succeed`).

## T1 — solid-RAR demux mutation — **DONE (#184)**

Static fixtures `basic_solid__.rar` / `basic_solid__rar4.rar` are mutated with
the same typed-error-or-success invariant as the declarative corpus. Declarative
CORPUS still does not emit `-s` (solid); that remains a possible later widen,
but the demux path the RAR review cared about is under the generative net.
Encrypted-header / multi-volume static fixtures are still outside mutation —
folded into **T7** (matrix edges), not reopened as T1.

## T2 — the seek-interleaving property test stops at XZ (PAY — cheap)

`lzip.py` (backward trailer scan, member table) and `unix_compress.py` (CLEAR
seek points, "after-placement" semantics) implement the same class of
seek-point arithmetic as XZ, and the stream-decoder review's F1 (seek-point
collision) was found in this layer. Lzip has targeted example tests but no
randomized interleaving equivalent of `test_xz_seek_interleaving_matches_plaintext`.
The test shape already exists; parametrizing it over lzip (and `.Z` where
seekable) is an afternoon. **PAY before 0.2.0** — still open @ `8cc3ea5`.

## T3 — benchmark-gate data cases: RAR / encrypted / accelerator paths unmeasured (PAY)

`test_benchmark_gate.py` still has no RAR, encrypted, or accelerator *data*
cases. Whatever D1's re-worded claim ends up saying, it can only be honest for
paths the gate measures. `review/STATUS.md` tracks this as perf P6 remainder;
coupling to D1 remains. **PAY** — still open.

## T4 — free-threaded coverage is still core-only, and `*_if_available` is still untested under threads (KEEP scope / PAY one test)

- The `3.13t` job still runs core-only; optional backends are honestly scoped
  out in the job comment and threat-model C4. **KEEP** that scope.
- No test calls `members_report_if_available()` from multiple threads
  (`test_concurrent_multithread.py`: zero hits). **PAY** one
  `concurrent_reader`-marked barrier test — still open.

## T5 — remaining fault-injection gaps (KEEP, recorded — pay opportunistically)

- `os.symlink`-unsupported destination (EPERM injection): still no test.
- True ENOSPC/`dst.write` mid-stream injection: behavioral class covered via
  ResourceLimitError; raw-OSError flavor is not.
- Case-insensitive collision semantics covered by O2 tests.

**KEEP with justification**; listed so the next test pass picks them up.

## T6 — no randomized/stateful concurrency stress (KEEP)

Barrier tests trigger known interleavings; no `hypothesis.stateful` machine
over the reader API. C4's claim is deliberately narrow; S4 rework shrank the
state space. **KEEP past 0.2.0**; becomes PAY if parallel extraction scheduling
lands.

## T7 — corpus matrix thin spots after oracle retirement (AUDIT — half-day)

Declarative corpus remains the primary conformance net. Spot-check @ `8cc3ea5`:

- **ISO appears only in `basic`** — encoding, symlinks, permissions, large
  never build ISO variants.
- 7z/RAR present across most entries, but **encrypted-header 7z** and
  **multi-volume** still live mainly in static fixtures / dedicated tests
  (outside sweep+mutation), same shape T1 had for solid before #184.
- Single-file compressors and `gz-meta` remain well covered.

**PAY a half-day audit**: enumerate format×feature against the sweep's
reachable set, extend where the builder makes it cheap (ISO with symlinks +
encoding at least), and record deliberate exclusions in `sample_archives.py`
comments.

## What is actually fine (so the next review doesn't re-derive it)

- The layered fuzz story (mutation + Hypothesis + Atheris, accelerators off
  with the hang rationale written down) is coherent and unusually complete.
- The three-dependency-config discipline is real in CI and CONTRIBUTING.
- The declarative corpus + sweep remains a genuine regression net; T1 widened
  solid-RAR intake; T2/T7 remain the widenings left.
- Adversarial name corpus, ZipCrypto disambiguation, accelerator-shutdown
  subprocess harness, EOF-probe tests: all present and behaviour-focused.
