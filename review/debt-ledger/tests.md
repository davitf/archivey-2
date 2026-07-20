# Test-strategy holes — measured against the bug classes prior reviews found

The lens (backlog Topic 4): three security reviews repeatedly concluded "no
test in the suite catches this". The question is not line coverage — it is
whether the suite's *generative* nets (corpus sweep, mutation fuzz, Atheris,
Hypothesis) reach the code where review-found bugs actually lived. References:
`main` @ `7bb862b`.

## First, what got fixed since the old `tests.md` (credit)

- **F5/T4 randomized seek** — landed for XZ:
  `test_seekable_streams.py:507-560` is a Hypothesis interleaving test
  (size-probe/seek/read op sequences vs plaintext oracle, multi-stream
  inputs). This is exactly the test shape whose absence hid the stream-decoder
  F1 crash.
- **F4 both-idioms truncation** — landed for `.Z`:
  `test_codecs.py:330-344` exercises both the chunked idiom and single-shot
  `read()`/`readall()` against deferred truncation errors.
- **T2 BaseException-mid-materialization** — the C1 fix is structural now
  (`base_reader.py:817-828` resets election state on BaseException) with the
  matching test landed in the old review's follow-up.
- **T3 atomic-replace negative test** — partially landed:
  `test_extraction.py:616-640` proves a mid-extraction failure under REPLACE
  leaves the old file untouched and no `.archivey-tmp-*` behind.
- **T5 Atheris gate** — landed and extended (7z/RAR headers, open+members,
  detection, ZIP/TAR/ISO, standalone codecs; PR-sharded + change-guarded
  nightly).

## T1 — the mutation/sweep nets cover only the *declarative* corpus; solid-RAR demux is example-tested only (PAY)

`test_mutation_fuzz.py` and `test_corpus_sweep.py` both iterate `CORPUS` from
`sample_archives.py` (`test_mutation_fuzz.py:77,118`,
`test_corpus_sweep.py:39,65`). The static fixtures under `tests/fixtures/rar/`
— **solid** RAR4/RAR5, encrypted-header, multi-volume, file-version, blake2sp
— are outside both nets: never swept by the conformance matrix, never mutated.
Atheris's RAR targets cover header parse and open+members, not the solid
ALL-pipe data path.

Why this is the top test hole: the RAR review's findings (archived F1–F6) and
the open-issues **P6** risk ("solid demux ↔ unrar emission-policy coupling —
easy to desync on new member kinds") live precisely in the solid demux loop
(`rar_reader.py:578-649`: `pipe_offset` accounting, `-ver` alignment,
0-byte-emission member kinds). That code is guarded today only by
example-based fixture tests (`test_rar_reader.py`). A truncated/bit-flipped
*solid* RAR exercising the demux under damage is the class of input prior
reviews kept finding bugs with, and no generative net produces it.

**PAY before 0.2.0** (bounded, concrete): extend the mutation harness to also
mutate a curated subset of the static fixtures (solid RAR4/RAR5 at minimum —
the harness's invariant "typed error or success, never raw, never hang"
transfers unchanged), and/or teach the declarative RAR builder `-s` so solid
enters `CORPUS`. Cheap; reuses existing machinery. (This also feeds S3: if
the pass driver is later unified, this net guards the migration.)

## T2 — the seek-interleaving property test stops at XZ (PAY — cheap)

`lzip.py` (backward trailer scan, member table) and `unix_compress.py` (CLEAR
seek points, "after-placement" semantics per its own docstring) implement the
same class of seek-point arithmetic as XZ, and the stream-decoder review's F1
(seek-point collision) was found in this layer. Lzip has targeted example
tests (`test_seekable_streams.py:267` etc.) but no randomized interleaving
equivalent of `test_xz_seek_interleaving_matches_plaintext`. The test shape
already exists; parametrizing it over lzip (and `.Z` where seekable) is an
afternoon. **PAY before 0.2.0** — this is the documented "one ordering hid the
crash" lesson applied to the two decoders that didn't get the test.

## T3 — benchmark-gate data cases: RAR / encrypted / accelerator paths unmeasured (PAY — already tracked as perf P6 remainder)

`test_benchmark_gate.py` has no RAR, encrypted, or accelerator *data* cases
(grep: zero hits). Whatever D1's re-worded claim ends up saying, it can only
be honest for paths the gate measures. `review/STATUS.md` already tracks this
("P6 remainder"); the ledger adds only the coupling: **this is a dependency of
D1**, so it belongs in the pre-0.2.0 window, not the someday pile. **PAY.**

## T4 — free-threaded coverage is still core-only, and `*_if_available` is still untested under threads (KEEP scope / PAY one test)

- The `3.13t` job still runs `--no-dev` core-only (`ci.yml:183-191`), so
  ISO/pycdlib and accelerators never run free-threaded. Unlike 2026-07-12,
  this is now **honestly scoped in public**: the job comment (`ci.yml:168`)
  and threat-model C4 both say optional backends are not claimed covered.
  **KEEP with that recorded justification** (a dedicated optional-deps
  free-threaded job is real work; the claim is already narrow).
- The old T1's second half is still true: no test calls
  `members_report_if_available()` from multiple threads
  (`test_concurrent_multithread.py`: zero hits). It runs outside the
  materialization election by design, which is exactly why it deserves one
  barrier test. **PAY** (one test, `concurrent_reader`-marked so it rides the
  3.13t job).

## T5 — remaining fault-injection gaps (KEEP, recorded — pay opportunistically)

- `os.symlink`-unsupported destination (EPERM injection → per-member failure,
  no copy-the-target fallback): still no test (grep for monkeypatched
  symlink/EPERM: none). The no-fallback behavior is spec'd and gotcha'd.
- True ENOSPC/`dst.write` mid-stream injection: the *behavioral class*
  (old-file preserved, temp unlinked) is covered via the ResourceLimitError
  path (`test_extraction.py:616`); the raw-OSError flavor is not.
- Case-insensitive collision semantics are covered by the O2 implementation's
  tests (collision tracking is now platform-independent under
  STRICT/STANDARD, which retired most of old T6's "only fails on macOS CI"
  risk).

Individually small; none guards a public claim. **KEEP with justification**
(the atomic-write invariant has a negative test; the rest is
defense-in-depth), listed here so the next test pass picks them up.

## T6 — no randomized/stateful concurrency stress (KEEP)

Old T7 stands: barrier tests trigger known interleavings; there is no
`hypothesis.stateful` machine over the reader API. The concurrency surface
has since been *simplified* (token rework, single-snapshot publication), and
C4's claim is deliberately narrow. **KEEP past 0.2.0 with justification**
(claim-scope narrowness + the S4 rework shrinking the state space); becomes
PAY if parallel extraction scheduling (future per C4) ever lands.

## T7 — corpus matrix thin spots after oracle retirement (AUDIT — half-day)

The declarative corpus is now the primary conformance net (oracle #46
retired to a regression gate). Matrix reading of `sample_archives.py:307-345`:

- **ISO appears only in `basic`** — encoding, symlinks, permissions, large
  never build ISO variants, so the sweep exercises pycdlib on trivial trees
  only (the pycdlib cycle-guard bug came from a *non-trivial* tree).
- 7z/RAR are present across most entries (good), but **encrypted-header 7z
  (`-mhe=on`) and multi-volume** live only in static fixtures / dedicated
  tests, i.e. outside the sweep+mutation nets (same shape as T1).
- Single-file compressors and `gz-meta` are well covered.

**PAY a half-day audit**: enumerate format×feature against the sweep's
reachable set, extend where the builder makes it cheap (ISO with symlinks +
encoding at least), and record the deliberate exclusions in
`sample_archives.py` comments so the matrix's edges are chosen, not
accidental.

## What is actually fine (so the next review doesn't re-derive it)

- The layered fuzz story (mutation + Hypothesis + Atheris, accelerators off
  with the hang rationale written down) is coherent and unusually complete.
- The three-dependency-config discipline (`[all]` / `[all-lowest]` /
  `[core-only]`) is real in CI and CONTRIBUTING, and it is the right axis for
  this library.
- The declarative corpus + sweep remains a genuine regression net; nothing
  here proposes replacing it — only widening what flows through it (T1/T7).
- Adversarial name corpus, ZipCrypto disambiguation, accelerator-shutdown
  subprocess harness, EOF-probe tests: all present and behaviour-focused.
