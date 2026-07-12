# Theme 4 — Tests (scenario gaps, not line coverage)

The suite is strong where the project decided to invest: a declarative corpus + conformance
sweep, a corpus mutation harness (`test_mutation_fuzz.py`), Hypothesis property tests
(`test_property_safety.py`), barrier-synchronized multithread tests, and a dedicated
free-threaded CI job. 87% line coverage. The gaps below are *scenario* gaps — cases the current
tests structurally cannot reach.

## T1 — Free-threaded coverage misses `get_members_if_available()` and is core-only (medium)

The `3.13t` CI job (`ci.yml:149-171`) runs `uv sync --no-dev` + `pytest -m concurrent_reader`.
Two blind spots:
- **`--no-dev`** means only zero-dep-core backends run free-threaded: directory, ZIP, TAR,
  single-file, native-7z. **ISO (pycdlib) never runs under free-threading** — yet it has a
  shared `_cdfp` and a `LockedStream`, and the pycdlib deque cycle-guard is process-global.
  Its concurrency correctness is asserted only under the GIL.
- I found **no test calling `get_members_if_available()` from multiple threads** (grep of both
  concurrency test files: zero hits). That method runs outside the materialization election
  (concurrency.md C2) and, for directory, mutates `_uname_cache`/`_gname_cache` unguarded
  (`directory_reader.py:230-248`). A `concurrent_reader` test that fans N threads into
  `get_members_if_available()` on a directory with many distinct uids would exercise the race.

Proposed harness: add to `test_concurrent_multithread.py`, marked `concurrent_reader` so it lands
in the `3.13t` job:
```python
def test_multithread_get_members_if_available_directory(tmp_path):
    # tree with files owned across several uids (or monkeypatched pwd) to force cache writes
    reader = open_archive(tree, member_streams=MemberStreams.CONCURRENT)
    barrier = threading.Barrier(N)
    def worker(): barrier.wait(); return reader.get_members_if_available()
    # assert every thread returns an equal-length, consistent listing; no crash/corruption
```

## T2 — No BaseException-mid-materialization test (medium; pairs with C1)

There is no test that injects a `KeyboardInterrupt`/`MemoryError` *during* `_iter_members()` and
then asserts the reader is still usable (non-concurrent) or that CONCURRENT waiters wake
(concurrency.md C1). This is a fault-injection gap that a red test would pin before the C1 fix:
```python
def test_keyboardinterrupt_during_materialization_does_not_wedge(...):
    reader = ...  # backend whose _iter_members can be patched to raise KeyboardInterrupt once
    with pytest.raises(KeyboardInterrupt): reader.members()
    # second call must NOT raise "another materialization is already in progress"
    assert reader.members()  # succeeds after the transient interrupt
```

## T3 — Extraction fault injection is narrow (medium)

Present and good: EILSEQ on `os.replace` (`test_extraction.py:243`), EXDEV on `os.link`
(`:1062`). Missing:
- **Mid-write failure / ENOSPC:** a source stream (or `dst.write`) that raises partway through
  `_write_file_atomic`. Assert (a) the `.archivey-tmp-*` file is unlinked, (b) an existing
  destination at that path is byte-for-byte untouched, (c) the result is `FAILED` (under CONTINUE)
  or re-raised (under STOP). The atomic-replace design (`extraction.py:879-907`) is the safety
  guarantee and it has no negative test.
- **`os.symlink` unsupported** (filesystem without symlink support): the coordinator's documented
  "fail via OnError, no copy-the-target fallback" path (`extraction.py:558-559`) — inject
  `os.symlink` raising `OSError(EPERM)` and assert the per-member failure, no fallback file.

## T4 — Seekable-decoder seek math has no property test (medium)

`xz.py` (85%) and `lzip.py` implement backward index scans, synthetic per-block XZ streams, and a
merge-sort seek-point table — the highest-density arithmetic in the codebase, and exactly where
line coverage lies about correctness. A property test would be high-value:
```python
@given(multi_stream_xz_bytes())  # 2+ streams, several blocks, varied sizes
def test_xz_random_access_matches_full_decompress(data, offsets):
    full = lzma.decompress(...)  # oracle
    s = XzDecompressorStream(BytesIO(data), seekable=True)
    for off in offsets + [SEEK_END-probes, rewinds]:
        s.seek(off); assert s.read(n) == full[off:off+n]
```
Same shape for lzip. The mutation harness proves *never-crash*; this would prove *correct-bytes*
under scattered seeks — the class of bug (off-by-one in `_round_up_4`, a wrong `decompressed_start`
accumulation) that survives a forward-read-only test.

## T5 — Coverage-guided fuzzing of the 7z/RAR parsers is not yet a gate (roadmap, expected)

`tests/fuzz_sevenzip_parser.py` + the corpus mutation harness exist, but the Atheris
coverage-guided entry gate the roadmap names (`PLAN.md` Phase 6 entry criteria; threat-model O5)
isn't stood up. The native 7z parser (`sevenzip_parser.py`) is pure-Python parsing of hostile
input — the exact thing that class of fuzzing is for. Not a bug; a scheduled-work reminder that
should land *with* the native readers, not after.

## T6 — Platform-semantic scenarios (low-medium)

CI runs macOS + Windows, but I don't see tests for:
- **Case-insensitive filesystem collisions:** two members `A.txt` and `a.txt` extracting to the
  same on-disk path on macOS/Windows. Under `OverwritePolicy.ERROR` the second should error; under
  REPLACE it overwrites. Currently untested; the coordinator keys `written_paths` by exact `Path`,
  which on a case-insensitive FS is a different key than the FS's identity.
- **Windows junction round-trip** beyond the directory backend's read path.

## T7 — Concurrency stress is triggering, not randomized (low)

The multithread tests use `threading.Barrier` with fixed small thread counts to *trigger* a
specific interleaving once. That's the right tool for a known race, but there's no long-running
randomized stress (many iterations, randomized op mix of open/read/close/members/get/scan across
threads) that would surface rare interleavings. A `hypothesis.stateful` `RuleBasedStateMachine`
over the reader API, run under the `3.13t` job, would be the highest-leverage addition for the
concurrency surface.

## What's already well covered (credit where due)

- The declarative corpus × cross-format sweep (`test_corpus_sweep.py`) is a genuine regression net.
- ZipCrypto multi-password disambiguation (STORED + collision) has focused tests.
- The accelerator shutdown / single-library invariant is guarded in a subprocess harness
  (`test_accelerator_shutdown.py`) — unusually thorough for a class of bug most libraries never test.
- Adversarial name corpus, bidi controls, timestamp edge cases, truncation translation — all present.
