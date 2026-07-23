# Known issues

> See also [Compression-library analysis](library-analysis.md) for which library backs each
> codec and why — including why `rapidgzip` is the single accelerator library (the issue below)
> and why an `indexed_zstd` zstd accelerator would face the same constraint.

## stdlib `tarfile` treats a corrupt non-first header as clean end-of-archive

`tarfile.TarFile.next()` re-raises `InvalidHeaderError` only when it occurs at offset 0;
a corrupt member header anywhere later is swallowed and iteration simply ends — so
mid-archive corruption produces a **silently shortened listing**, never a
`CorruptionError`. Confirmed against a corrupted-checksum fixture and a corrupted
`.tar.gz` whose garbage decode parses as an invalid header (deep review W1).

Archivey's backstop is the end-of-archive check in `TarReader._verify_tar_eof`
(`decide-strict-archive-eof-default`, Option F). When the stopped scan lands on a
**rejected (non-null) header block**, archivey raises `CorruptionError` **by default**;
a tar that merely ended on a member boundary without the two-block null trailer is warned
via `ARCHIVE_EOF_MARKER_MISSING` (WARNING by default; `strict_archive_eof=True` escalates
to `TruncatedError`). In random-access mode the rejected-header detection uses
`_EofProbeStream`: after the header scan it inspects the block tarfile's final header
attempt returned (always one more `next()` before stop), so it catches the case **even
when the bad header is the archive's final block** — including after a GNU sparse member —
without seeking back (no re-decompression on a compressed source).

**Streaming limitation (open).** In forward-only streaming (`streaming=True`), tarfile's
`_Stream` hides its header reads, so `_EofProbeStream` is unavailable and detection falls
back to the trailing-block check. A rejected **final** header (a corrupt header as the
archive's last block, nothing after) is therefore misclassified as `observed_kind="absent"`
and surfaces as a missing-trailer warning, not `CorruptionError`. Random access catches
this case. A native TAR header walker (the 7z/RAR strategy applied to TAR, open-issues P3)
would validate each header at its offset and close the streaming gap. Documented for users
in `docs/formats.md` and `docs/gotchas.md`.

## Importing the ISO backend patches pycdlib process-globally (by design)

`import archivey` eagerly imports the ISO backend to register it, and that import installs a
directory-cycle guard **into pycdlib's own namespace**: `iso_reader._install_pycdlib_directory_cycle_guard()`
replaces `pycdlib.pycdlib.collections` with a proxy whose `deque` subclass drops a directory
record whose extent it has already scheduled. Without it, a corrupt/crafted ISO whose directory
records close a cycle (a child extent pointing back at an ancestor) loops forever in pycdlib's
plain-`deque` tree walk — the mutation harness found a Joliet case (see
`test_pycdlib_directory_cycle_does_not_hang`).

The guard is confined to pycdlib (not a global `collections.deque` swap), installed once and
permanently, and is transparent on well-formed images (valid trees never revisit an extent). The
one thing to be aware of: a program that **also uses pycdlib directly** in the same process will
see archivey's guarded `deque` in pycdlib's namespace too. That is a deliberate trade — hang-safety
on hostile input over leaving another library's pycdlib untouched — and the guard is a strict
superset of pycdlib's own behaviour on valid trees, so it does not change correct results.

## Random-access accelerators on macOS (resolved)

**Status:** resolved. archivey uses a single accelerator library — `rapidgzip` — for both gzip
and bzip2, and closes every accelerator object via a `weakref.finalize` guard. With those two
measures the accelerators run cleanly on Linux, Windows, and macOS, so `AUTO` enables them on
every platform. This note records the two distinct bugs behind the long investigation.

### Symptom

With the `[seekable]` accelerators installed, a process that used them could abort with
**SIGABRT (exit code 134)** at interpreter shutdown — *after* all work had completed — with
either of:

```
Detected Python finalization from running rapidgzip thread.
terminate called without an active exception
```
```
malloc: *** error for object 0x...: pointer being freed was not allocated
```

### Bug 1 — an accelerator object must be *closed*, not just joined

`rapidgzip` / `indexed_bzip2` spawn **C++ worker threads** (`std::thread`s, invisible to Python's
`threading` module). Each installs a guard that calls `std::terminate()` if a worker thread is
still running when the interpreter is finalizing. The decisive detail: **`join_threads()` does
not stop the worker thread — only `close()` does** (the library's own message says to "close all
… objects"). So a stream finalized **without being closed** aborts, on every platform. Measured
by `tests/test_accelerator_shutdown.py` (rapidgzip, both codecs × intact/corrupt/truncated ×
cleanup, each in its own subprocess); the input variant is irrelevant — only finalization
matters:

| Cleanup strategy | Result |
|---|---|
| **closed** — `read()`, then `join_threads()` + `close()` during the run | clean |
| **raw cycle_gc** — raw object reclaimed by the cyclic GC mid-run, never closed | **abort** |
| **raw unclosed** — raw object finalized at interpreter shutdown, never closed | **abort** |
| **guarded cycle_gc / unclosed** — same two paths, but a `weakref.finalize` guard **closes** the object on finalization | clean |

**Fix:** `_AcceleratorStream` (in `archivey.internal.streams.codecs`) wraps every accelerator
object and installs a `weakref.finalize` guard that **closes** the raw object exactly once — when
the wrapper is collected (cyclically or not) or at interpreter exit — holding a strong reference
so the close always runs before the object is freed. `close()` on the wrapper triggers the same
guard early. (An earlier version of the guard called `join_threads()` only, which is insufficient
— that was the first half of the macOS abort.)

### Bug 2 — rapidgzip and indexed_bzip2 cannot coexist in one process

After Bug 1 was fixed, macOS *still* aborted — but as a `malloc … pointer being freed was not
allocated` heap corruption, and only when **both** `rapidgzip` and `indexed_bzip2` were
importable. `scripts/dual_accelerator_repro.py` isolates it (no archivey, no pytest): decompressing
through **both** libraries in one process crashes ~100% of the time on macOS, while using either
one alone — even with both imported — never crashes. The two libraries are by the same author and
statically bundle a large overlapping C++ core; on macOS, dyld coalesces their duplicate weak C++
symbols across the two dynamic libraries, so one module's allocator can free the other's objects.

**Fix:** use only `rapidgzip`. Its Python package bundles the specialized bzip2 decoder as
`rapidgzip.IndexedBzip2File`, so archivey routes **both** gzip and bzip2 through rapidgzip and
never imports the standalone `indexed_bzip2` package. The `[seekable]` extra depends on
`rapidgzip` alone. With a single accelerator library in the process, the collision cannot happen.
`tests/test_accelerator_shutdown.py::test_archivey_uses_single_accelerator_library` guards against
regressing this (it decompresses both codecs through archivey in a subprocess and asserts
`indexed_bzip2` is never imported).

This matches the library author's own guidance. From
[mxmlnkn/librapidarchive](https://github.com/mxmlnkn/librapidarchive):

> I am not sure how well the rapidgzip and indexed_bzip2 Python modules work when loaded at the
> same time. There may be name collisions resulting in problems. … Currently, I am sidestepping
> this issue in ratarmount by including indexed_bzip2 in the rapidgzip Python package because it
> is trivial and low-overhead to do so. **So, if you need to use both, depend on rapidgzip for
> now.**

Note there are two ways rapidgzip can decode bzip2: `rapidgzip.IndexedBzip2File` (the
**specialized** indexed_bzip2 code bundled into the rapidgzip Python package — full feature and
performance parity) and `rapidgzip.RapidgzipFile` opening a `.bz2` directly (a **generic**
algorithm that, per the author, "has more memory overhead and might be slightly slower"). archivey
uses `IndexedBzip2File` for parity with the standalone package.

### Bug 3 — rapidgzip terminates the process when its Python source raises (open)

**Status: open upstream defect** (present in rapidgzip 0.16.0, the current and floor version).
When a rapidgzip object decodes from a **Python file object** and any callback into that object
raises — e.g. the stream was closed underneath it — the C++ layer throws
`std::invalid_argument` ("Cannot convert nullptr Python object to the requested result type")
through a `terminate()` boundary and **aborts the process** (SIGABRT). This fires on `read()`,
on `close()`, and on the GC-time finalize guard alike, so no Python-level `try/except` — not
even the Bug 1 guard's — can contain it, and archivey's reader-boundary error translation never
gets a chance to run.

**Mitigation in archivey:** never kill the source underneath a live accelerator stream. The
single-file reader's `_close_archive` deliberately does **not** close the (non-owning)
`SharedSource` behind stream-source member streams, so `reader.close()` with a member stream
still open cannot trigger the abort (and member streams stay readable after reader close, as
with every other backend). The remaining exposure — the **caller** closes their own source
stream while an accelerator-backed member stream is still in use — predates the SharedSource
retrofit (the accelerator used to sit directly on the caller's stream) and can only be fixed
upstream. Path sources are unaffected (rapidgzip owns an independent handle). The stdlib codec
fallbacks raise a normal `ValueError`, which the reader boundary translates to
`UnsupportedOperationError`.

### The canary

`tests/test_accelerator_shutdown.py` asserts the contract for Bug 1: the **closed** case and the
two **guarded** finalization paths exit cleanly on every platform (if they ever abort, archivey's
own cleanup is broken), while the **raw** `cycle_gc` / `unclosed` paths abort. If a future
`rapidgzip` release stops aborting on a raw, never-closed object (e.g. it closes/joins in its
destructor), the raw-case assertions **fail** — the signal that the close-on-finalize guard is no
longer load-bearing and the wrapper could be simplified.

### Debugging tools

- `scripts/dual_accelerator_repro.py` — confirms the two-library coexistence crash (Bug 2) and
  that routing both codecs through rapidgzip alone is safe.
- `scripts/accel_leak_trace.py` — runs the test suite with the accelerators force-enabled,
  records each accelerator stream's creation stack, and reports any left un-closed at shutdown.
- `scripts/macos_accelerator_debug.py` — characterises the finalization behaviour (Bug 1) across
  raw vs. guarded × cleanup strategies, each in its own subprocess.

## Intermittent `pyppmd` native aborts on valid PPMd streams (mitigated + stress CI)

**Status: root cause pinpointed to the pyppmd 1.3.0 `ThreadDecoder.c` rewrite (upstream
PR miurahr/pyppmd#126); mitigated in archivey by bounding every decode; not yet fixed
upstream (no issue filed there as of 2026-07-16 — ready-to-file draft in
`docs/internal/pyppmd-upstream-report.md`). Linux and Windows both affected (different
trigger shapes).** Not adversarial input — happy-path encode/decode of valid PPMd data.

### Root cause (pinpointed from the 1.2.0 → 1.3.1 sdist diff)

pyppmd runs `Ppmd7Decoder.decode` on a native worker thread. The 1.3.0 rewrite removed
the worker loop's input-empty stop condition, and `_ppmdmodule.c` translates
`max_length=-1` into an `INT_MAX` symbol budget — so on PPMd7 (no end mark) the worker
decodes **past the true end of the stream**, walking the vendored 7-Zip model on a
desynchronized range coder until the heap corrupts. The after-eof guard 1.3.0 added went
to the cffi backend only; the C extension has none, so `decode(b"\0", -1)` after eof
restarts the runaway worker on finished state (the hottest trigger). Sized decodes stop
exactly at the payload boundary, which is why they never crash — and why py7zr (which
always passes `max_length`) never sees this. Full analysis, crash-rate tables, and
suggested upstream fixes: `docs/internal/pyppmd-upstream-report.md`.

### Windows: `STATUS_HEAP_CORRUPTION` on fresh PPMd children

On `windows-latest` the suite has intermittently aborted during
`tests/test_sevenzip_reader.py::test_py7zr_codec_fixtures_roundtrip` with
`Windows fatal exception: code 0xc0000374` (`STATUS_HEAP_CORRUPTION`). Re-runs of identical
commits often pass. Early reports noted Windows/py3.14; isolation later pinned the same
abort on **Windows/py3.11** as well (`pyppmd==1.3.1` win_amd64). Windows/py3.14 can still
pass on the same commit that fails py3.11.

Per-label subprocess isolation (#80) + a dedicated stress run produced:

| Field | Value |
|-------|--------|
| Label / filters | `ppmd` / `("PPMD",)` |
| Exit | `0xC0000374` (`STATUS_HEAP_CORRUPTION`) |
| Library | `pyppmd` 1.3.1 |
| First pin phase | `read_member:nested/beta.bin:start` (after `alpha.txt` OK) |
| Stress pin (50×) | **2/50** on py3.11 at `read_member:alpha.txt:start`; 0/50 on py3.14 that run |
| Stack | `_open_member` → `skip_forward` / decode → `pyppmd` |

**Fresh PPMd-only subprocesses are enough** — prior pytest cases are not required. The
fixture is a py7zr-built solid PPMd archive from plain members (`b"alpha\n" * 100`,
`bytes(range(64)) * 16`).

### Linux: SIGSEGV / `malloc(): invalid size` after other-codec warmup

Independently, stress on Linux reproduced a **highly flaky** native abort when other 7z
codecs are exercised in the same process **before** PPMd (`warmup_codecs` scenario):

| Observation | Detail |
|-------------|--------|
| Rate | ~**10/30** children (~1/3) in one local soak; also seen on a single first run |
| Signals | `SIGSEGV` (−11) and `SIGABRT` (−6) with `malloc(): invalid size (unsorted)` |
| Typical phase | PPMd read after LZMA2/Deflate/Bzip2 warmup (`read_member:…:start` or stream open) |
| `fresh_baseline` alone | 0/20 crashes in the same soak |
| Raw `pyppmd` encode/decode alone | 0/40 subprocesses |
| Raw archivey `PpmdDecompressorStream` alone | clean in short soaks |
| Warmup **without** PPMd (LZMA2/Deflate/Bzip2 only) | **0/30** crashes |
| Same warmup **then** PPMd | **10/30** crashes |

So the Linux abort **is PPMd-related** — other-codec warmup alone does not fire it; PPMd
after that warmup does. It still looks like process-wide native state interacting with
`pyppmd` (not a pure “any native codec” crash). Windows can also fail on a minimal fresh
PPMd 7z child; stress runs often show `raw_*` clean and `warmup_codecs` hot on both OSes.
Treat per-scenario rates as the comparison table.

### Version matrix (Linux, `warmup_codecs`, unbounded `decode(..., -1)`)

To check whether the native abort is recent, the same stress was run across published
`pyppmd` versions with archivey’s PPMd adapter forced back to unbounded `max_length=-1`
(the pre-`unpack_size` behavior). 40 children each:

| pyppmd | native crashes | other failures | passes |
|--------|----------------|----------------|--------|
| 1.1.1 | **0**/40 | 27 (CRC mismatch on solid 2nd member) | 13 |
| 1.2.0 | **0**/40 | 27 (same CRC pattern) | 13 |
| 1.3.1 | **12**/40 (`SIGSEGV`/`SIGABRT`) | 0 | 28 |

`pyppmd==1.3.0` has no installable artifact on this platform (PyPI 404 / resolver miss);
1.3.1 (2025-11-27) is the first 1.3.x wheel we could run. The 1.3.0/1.3.1 git delta includes
threaded-decoder buffer/EOF changes (`ThreadDecoder.c`, #126).

**Conclusion:** the Linux native abort reproduces on **1.3.1** and not on **1.1.1/1.2.0**
under the same unbounded decode path — the regression is the 1.3.0 `ThreadDecoder.c`
rewrite (see “Root cause” above). Older versions instead return wrong bytes (CRC fail)
on solid multi-member reads rather than aborting: their worker stopped at input-empty,
which prevented the runaway but also cut symbols short at chunk boundaries.

With the current `unpack_size`/`max_length` bound (see below), the same Linux
`warmup_codecs` soak was **0/80** crashes on 1.3.1 — so bounding decode is an effective
mitigation even on the crashy wheel.

**Version floor decision:** the `[7z]` extra now requires **`pyppmd>=1.3.1`**. Pinning
older is worse on every axis: 1.1.x/1.2.0 silently return *wrong bytes* on chunked
bounded decodes (quiet data corruption beats a crash only if you never notice it),
`py7zr` ≥1.1 hard-requires `pyppmd>=1.3.1` (dependency conflict with the `[7z-write]`
extra and the test oracle), and 1.3.1 is the first line with CPython 3.14 wheels. With
the floor raised, the 1.1.x premature-eof recovery pumps were removed from
`PpmdDecoder.flush` — it now injects at most the one documented extra NUL and reports
anything still missing as truncation.

Repro (non-blocking stress entry points):

```bash
uv run --no-sync python scripts/ppmd_native_stress.py 30 --scenarios warmup_codecs
uv run --no-sync pytest -m ppmd_native_stress -k warmup --timeout=600 -o addopts=
```

**Minimal upstream-facing repro (no archivey):** `scripts/pyppmd_crash_repro.py`
depends only on ``pyppmd`` (+ stdlib). Two crash families on 1.3.1:

| mode | what | ~crash rate (5 cycles/child) |
|------|------|------------------------------|
| `extra-null` | sized to eof, then `decode(b"\\0", -1)` | ~40% (up to 30/30 seen) |
| `overshoot` | `decode(packed, -1)` only | ~15–25% (19/30 seen) |
| `sized-safe` / `pre-eof-null` / `skip-after-eof` | controls | 0% |
| `underfed-sized` / `hostile-tail` | adversarial-shape controls (truncation, garbage tail) | 0% |

```bash
pip install 'pyppmd==1.3.1'
python scripts/pyppmd_crash_repro.py 30 --mode extra-null
python scripts/pyppmd_crash_repro.py 30 --mode overshoot
python scripts/pyppmd_crash_repro.py 30 --mode sized-safe
```

**Archivey mitigation:** on the 7z/ZIP paths we avoid both crash families by being
careful — (1) always pass folder/member ``unpack_size`` as ``max_length`` (no PPMd7
``-1`` overshoot); (2) never call native ``decode`` with ``max_length=-1`` after eof;
(3) at compressed EOF, ``flush`` injects at most **one** documented extra NUL (bounded
by remaining size) and reports anything still missing as ``TruncatedError`` — fabricated
input is never pumped in a loop, so truncated/hostile data cannot be silently completed
with garbage.

**The exact bound is what matters — “bounded” is not enough.** A/B soaks showed sized
requests that exceed the stream's true remaining output by ≳64 KiB crash 1.3.1 **without
any ``-1``** (`oversized` mode: +65536 over → 13/20 and 10/20 in two soaks; +64 / +4096
over → 0/20 each; large multi-chunk members with the exact bound → 0/20). Consequences:

- **Unsized PPMd7 is rejected at construction** (`ValueError`): with no end mark and no
  declared size there is no safe request size — and no correct output boundary anyway.
  The 7z header always provides the folder size, so no product path is affected.
- **Unsized PPMd8 stays supported** (end mark stops the native worker on valid data);
  it decodes via bounded 64 KiB requests in a drain loop, never ``-1``. ZIP always
  passes the member size in practice.
- **Residual hostile-input gap (upstream-only fix):** a crafted 7z/ZIP header that
  inflates ``unpack_size`` ≳64 KiB past the member's true content puts the one decode
  call into the crashy class. Archivey cannot detect the lie before decoding; this
  stays a threat-model item on par with other native-codec robustness assumptions
  until pyppmd is fixed. (Small inflations measured cold; CRC checks catch the
  garbage-output side after the fact.)
The required-suite Windows PPMd roundtrip skip was removed under this contract; the
non-blocking stress job still watches for regressions. Adversarial shapes a damaged or
hostile archive can force (truncation, early close mid-member, inflated declared size
with a garbage tail) are pinned as deterministic tests in
``tests/test_ppmd_raw_streams.py`` and as subprocess soak modes in
``scripts/pyppmd_crash_repro.py`` — all 0-crash on 1.3.1 with bounding in place.

### Mitigation in the required CI matrix

- Required-suite PPMd roundtrip runs on all platforms (including Windows); decode is
  bounded by folder unpack size.
- Other Windows codec labels keep per-label subprocess isolation.
- Default pytest excludes `-m 'not ppmd_native_stress'` so stress tests never fail the
  required suite.
- Deterministic **raw** PPMd coverage (no 7z) lives in `tests/test_ppmd_raw_streams.py`
  (always passes ``unpack_size`` for PPMd7).
- In-process PPMd7 create/destroy loops remain skipped on Windows (stress job covers that).

### Non-blocking stress check (investigation vehicle)

Workflow **PPMd native stress** (`.github/workflows/ppmd-native-stress.yml`) on every PR /
main push:

- `windows-latest` + `ubuntu-latest` × Python **3.11 and 3.14**
- `scripts/ppmd_native_stress.py` (ASCII-safe console I/O) + `pytest -m ppmd_native_stress`
- Exit non-zero when any child crashes (visibility only — **do not** make this a required
  check)

Default scenarios favour the **minimal surface**, then the original 7z baseline, then
warmup:

| Scenario | Surface | Notes |
|----------|---------|--------|
| `raw_pyppmd7` / `raw_pyppmd8` | bare `pyppmd` only | No archivey, no 7z |
| `raw_archivey_ppmd7` / `raw_archivey_ppmd8` | `PpmdDecompressorStream` / `open_codec_stream` | No 7z container |
| `fresh_baseline` | py7zr PPMd 7z + archivey read | Original CI fixture |
| `warmup_codecs` | LZMA2→Deflate→Bzip2 then PPMd | Linux ~1/3 abort repro |
| `same_process` / `fresh_varied` | optional | Reuse / payload-shape axes |

```bash
uv sync --group dev --extra all
uv run --no-sync python scripts/ppmd_native_stress.py
uv run --no-sync python scripts/ppmd_native_stress.py --scenarios raw_pyppmd7 raw_archivey_ppmd7
ARCHIVEY_PPMD_STRESS_ITERS=30 uv run --no-sync python scripts/ppmd_native_stress.py --scenarios warmup_codecs
```

### Next steps

- **File the upstream issue** — the ready-to-file draft (root cause, repro, crash-rate
  tables, suggested fixes) is `docs/internal/pyppmd-upstream-report.md`; the repro
  script is self-contained (`pyppmd` + stdlib). No matching issue existed upstream as
  of 2026-07-16.
- When a fixed pyppmd ships, run the verification checklist at the end of that report;
  the unbounded-decode guards in `PpmdDecoder` stay regardless (older wheels remain on
  PyPI, and bounding is correct behavior anyway).
- The earlier open questions (is it archivey's wrapper? the 7z path? warmup-only?) are
  resolved: it is pure `pyppmd` (crash reproduces with no archivey imports), warmup only
  shifts allocator layout, and sized decodes are structurally safe.

## `pyppmd` exit-after-green abort (`test_ppmd_raw_streams` teardown)

**Status: partially mitigated (2026-07-23).** Separate fingerprint from the
mid-decode / `warmup_codecs` / unbounded-`decode(..., -1)` bug above. The
**decode-time overshoot** (large NUL flush on truncated streams) is fixed. The
upstream **`Ppmd7T_Free` teardown race** on unfinished workers is **not**
eliminated — subprocess isolation contains it so it cannot kill the parent
pytest session, but children can still SIGSEGV after printing `ok`, and the
parent process can still intermittently exit-after-green. Required CI therefore
keeps `--allow-exit-after-green` for this module. Full lab notes:
`docs/internal/ppmd-exit-after-green-exploration.md`.

### Symptom (pre-mitigation)

1. Run `tests/test_ppmd_raw_streams.py` in its **own** pytest process (coverage off).
2. All tests pass; breadcrumb `sessionfinish exit=0`.
3. Child dies on interpreter teardown / pytest GC with SIGSEGV or
   `corrupted size vs. prev_size` (or, in a pyppmd-only env, often mid-suite
   during truncated-flush tests).
4. Fatal module lists often included `rapidgzip`/lz4/brotli — **red herrings**
   (import-time loads via `codecs._optional`); a pyppmd-only venv crashed *more*
   often (31/40) with only `pyppmd.c._ppmd` loaded.

### Root cause

Two stacked issues on pyppmd 1.3.x:

1. **Dangerous archivey pattern (fixed):** `PpmdDecoder.flush()` injected the
   documented extra NUL with `max_length = remaining unpack_size`. On a truncated
   mid-stream member that remaining is still large; `decode(b"\0", large)` is the
   same overshoot class as unbounded `decode(..., -1)` and corrupts the heap.
   Bare-`pyppmd` repro of that shape: **85/100** children SIGSEGV; with
   `max_length` capped to 64: **0/100**. Happy-path tests alone: **0/40**.
2. **Upstream `Ppmd7T_Free` race (contained, not fixed):** tearing down a decoder
   whose worker is still blocked on input can still poison the heap (documented in
   `pyppmd-upstream-report.md`). Unfinished-decoder adversarial tests run in
   **subprocess children** so a child abort cannot take down the parent session;
   `_run_ppmd_child` accepts teardown signal death after a green `ok` body.
   Parent-process exit-after-green remains possible → required CI soft-pass.

### Mitigation in archivey

- Cap extra-NUL recovery output to `_PPMD_EXTRA_NUL_MAX_OUTPUT` (64) in
  `PpmdDecoder.flush` / empty-`feed` NUL injection; at most one synthetic NUL;
  chunked empty drains when finishing a complete pack
  (`src/archivey/internal/streams/decompress.py`).
- Gate post-eof empty drains on ``pack_size``: drains run only when
  ``fed_compressed >= pack_size`` (**known-complete**). Unknown ``pack_size`` is
  treated conservatively (single capped NUL only — no chunked empty drains).
- Keep unfinished-decoder adversarial coverage in fresh subprocesses; tolerate
  child teardown abort after a successful body (`tests/test_ppmd_raw_streams.py`).

**Residual:** (1) `Ppmd7T_Free` teardown race — required CI still uses
`--allow-exit-after-green` for this module. (2) Declared-complete but internally
corrupt packs can still fill toward `unpack_size` via empty drains (container CRC
is the backstop). (3) `pack_size` must measure the same bytes `feed()` counts —
see `PpmdDecoder` docstring invariant.

### Verification (this investigation)

| Soak | Overshoot (large NUL) | Free-race residual |
|------|----------------------|--------------------|
| Bare half-pack + NUL(rem) | ~85/100 → **0/100** with cap 64 | n/a |
| Adversarial tests in subprocess | Contained | Child may still SIGSEGV after `ok` |
| Parent `test_ppmd_raw_streams` session | Soft-pass via `--allow-exit-after-green` | Intermittent exit-after-green possible |

Do **not** claim the Free race is gone until teardown is deterministic or
process-isolated by default. See also: exploration doc,
`scripts/ci_run_native_modules.py`, `.github/workflows/ppmd-native-stress.yml`.

## Intermittent Linux full-suite heap corruption (`[all]` / Hypothesis late crash)

**Status: open / intermittent.** Observed on GitHub Actions `ubuntu-latest` required CI
legs with `--extra all` (and `all-lowest`). **Not** the same bug as the pyppmd section
above: a green **PPMd native stress** workflow does not clear this, and the fatal stacks
are not inside a PPMd decode.

### Symptom

The required suite process dies with `Fatal Python error: Segmentation fault` (exit
139) or `Fatal Python error: Aborted` (exit 134). The Python stack at death is usually a
**late symptom** (heap already corrupt), not the corrupting call:

| Crash site (examples) | What it means |
|-----------------------|---------------|
| `Garbage-collecting` → `hypothesis/internal/charmap.py` during `test_property_safety.py::test_normalize_total_and_idempotent` (strategy validate / `characters`) | Hypothesis touches the allocator; prior native work already poisoned the heap |
| `Garbage-collecting` → `subprocess._close_pipe_fds` / `Popen` in `rar_unrar.open_unrar_p` (e.g. during `test_multi_volume_stream_materialization`) | Same: GC + new subprocess while the heap is bad |

Fatal logs list extension modules along the lines of:

`backports.zstd`, `lz4`, `_brotli`, `pyppmd.c._ppmd`, **`rapidgzip`**, `bcj._bcj`, `_cffi_backend`

### Where it does / does not show up

| Environment | Observation |
|-------------|-------------|
| `ubuntu-latest` × `[all]` / `[all-lowest]` × py3.11–3.13 (sometimes 3.14) | Intermittent process death mid-suite |
| Same matrix `[core-only]` | Clean (no `rapidgzip` / optional natives from `[all]`) |
| `macos-latest` / `windows-latest` `[all]` | Typically clean on the same commits (Windows also skips `unrar` data tests) |
| Local `uv run --no-sync pytest tests/ -q` after `uv sync --group dev --extra all` | Often green; **not a reliable one-shot repro** |
| PPMd stress job (isolated children; see section above) | Consistently green on recent PRs — different surface |

CI runners use **uv’s standalone CPython** (Linux builds report Clang in `sys.version`),
with pytest-cov enabled via `addopts` (`--cov=archivey …`).

### Why mid-decode PPMd stress does not clear the full-suite flake

`.github/workflows/ppmd-native-stress.yml` / `scripts/ppmd_native_stress.py`
default scenarios run short children aimed at mid-decode / `warmup_codecs` aborts
(no long pytest session, often no `rapidgzip`). A green result there means that
probe is quiet — not that a long `[all]` process is free of native heap damage.

The separate **exit-after-green** abort of `tests/test_ppmd_raw_streams.py` (green
session, then teardown SIGSEGV/SIGABRT) is documented above as **mitigated**; do
not conflate residual full-suite Hypothesis/RAR late crashes with that fingerprint.

### Leading suspects (unconfirmed)

1. **`rapidgzip`** in a long-lived pytest process (AUTO on under `[seekable]` / `[all]`),
   possibly after truncated/corrupt gzip/bzip2 paths exercised elsewhere in the suite
   (`tests/test_accelerator_shutdown.py` already documents raw rapidgzip abort-on-
   finalize in **subprocesses**; in-process corruption is a separate question).
2. **Interaction / allocator layout**: many natives loaded together + coverage + GC,
   with Hypothesis or `subprocess` merely the tripwire.
3. **Not** the gzip/zlib truncation-recovery *logic* itself — crashes predate a stable
   local repro of that change and do not stack in `DecompressorStream` / `verify.py`.

### CI bandage (not a root-cause fix)

Required `[all]` / `[all-lowest]` jobs split the suite (`.github/workflows/ci.yml`):

```text
# 1) Main suite — skip Hypothesis + dedicated accelerator/PPMd stream modules
pytest tests/ \
  --ignore=tests/test_property_safety.py \
  --ignore=tests/test_rapidgzip_deflate_zlib.py \
  --ignore=tests/test_accelerator_shutdown.py \
  --ignore=tests/test_accelerator_corruption.py \
  --ignore=tests/test_ppmd_raw_streams.py -q

# 2) Accelerator stream tests — one subprocess each (coverage off; breadcrumbs)
python scripts/ci_run_native_modules.py

# 3) PPMd raw streams — own subprocess (coverage off). Formerly soft-passed
#    exit-after-green; that abort is mitigated (capped NUL flush + subprocess
#    unfinished-decoder tests). Hard-fail like other native modules.
python scripts/ci_run_native_modules.py \
  --modules tests/test_ppmd_raw_streams.py

# 4) Hypothesis property-safety
pytest tests/test_property_safety.py -q
```

(1)↔(4) stops a corrupted main-suite heap from taking down Hypothesis in-process
(and vice versa). (2) keeps the heaviest in-process rapidgzip ON / truncated-corrupt
accelerator paths out of the long suite. (3) is PPMd raw streams in isolation —
see **`pyppmd` exit-after-green abort** above (mitigated).

**Why accelerator modules are not one combined pytest:** a single multi-module
process aborted on Ubuntu with every test green during `coverage.collector.flush_data`
/ GC (`corrupted size vs. prev_size`, exit 134). Per-module children + disabling
cov on that leg both harden the job and name the offender.

`PYTHONFAULTHANDLER=1` is set on these steps so fatal traces always dump.

This still does **not** claim the main suite is free of every native (AUTO/SEEKABLE
paths and py7zr/PPMd corpus remain). It is CI hygiene, not a product fix.

### How to reproduce / bisect (investigation recipe)

There is **no single-command reliable repro** yet. Use rate + A/B:

```bash
# Match CI-ish env (Linux preferred; uv CPython)
uv python install 3.11
uv sync --group dev --extra all
uv run --python 3.11 --no-sync python -c "import sys; print(sys.version)"

# 1) Baseline soak — expect rare exit 139/134, not every run
for i in $(seq 1 20); do
  echo "=== pass $i ==="
  uv run --python 3.11 --no-sync pytest tests/ -q \
    || { echo "FAILED pass $i rc=$?"; break; }
done

# 2) Same soak but keep Hypothesis out of the long process (CI bandage shape)
for i in $(seq 1 20); do
  uv run --python 3.11 --no-sync pytest tests/ \
    --ignore=tests/test_property_safety.py \
    --ignore=tests/test_rapidgzip_deflate_zlib.py \
    --ignore=tests/test_accelerator_shutdown.py \
    --ignore=tests/test_accelerator_corruption.py \
    --ignore=tests/test_ppmd_raw_streams.py -q \
    || { echo "main FAILED pass $i rc=$?"; break; }
  uv run --python 3.11 --no-sync python scripts/ci_run_native_modules.py \
    || { echo "accelerators FAILED pass $i rc=$?"; break; }
  uv run --python 3.11 --no-sync python scripts/ci_run_native_modules.py \
    --modules tests/test_ppmd_raw_streams.py \
    || { echo "ppmd-raw FAILED pass $i rc=$?"; break; }
  uv run --python 3.11 --no-sync pytest tests/test_property_safety.py -q \
    || { echo "property FAILED pass $i rc=$?"; break; }
done

# 2b) Hard soak of the PPMd raw-streams exit abort (matches non-required stress step)
uv run --python 3.11 --no-sync python scripts/ci_run_native_modules.py \
  --modules tests/test_ppmd_raw_streams.py --repeat 20

# 3) A/B: no rapidgzip in the environment (uninstall after sync)
uv run --python 3.11 --no-sync pip uninstall -y rapidgzip
# re-run soak (1); if crashes vanish, rapidgzip (or its use under AUTO) is implicated
# restore with: uv sync --group dev --extra all

# 4) A/B: no pyppmd (controls the other known native)
uv run --python 3.11 --no-sync pip uninstall -y pyppmd
# re-run soak (1); green PPMd stress already suggests this alone is insufficient
```

Useful while hunting:

- `PYTHONFAULTHANDLER=1` so fatal traces always dump.
- A pytest plugin or wrapper that logs the **last N nodeids** before death (crash stacks
  are late).
- Compare against CI artifacts for a red job: look for `Fatal Python error` +
  `Extension modules:` and the test named in the stack (Hypothesis vs RAR vs other).

**Known red CI fingerprints** (gzip-zlib truncation-recovery work, 2026-07; illustrative,
not a pinned commit contract):

- Run `29829920415` — Ubuntu py3.11/3.12 `[all]` SIGSEGV in Hypothesis charmap GC;
  py3.13/3.14 completed far enough to fail a separate RAR4 assertion.
- Run `29836095815` — after RAR4 fix: Ubuntu py3.11 SIGSEGV / py3.13 SIGABRT still in
  Hypothesis; other legs green.
- Run `29836326565` — with property-safety split: Ubuntu py3.11 SIGSEGV during RAR
  multi-volume `open_unrar_p` GC (main suite), proving Hypothesis isolation alone is
  incomplete.
- Run `29969446114` — after review follow-ups: Ubuntu py3.11/3.14 `[all]` SIGSEGV at
  ~63% (GC during fixture setup / `test_rar_oracle` ← `rarfile`/`cryptography` import),
  immediately after `test_ppmd_raw_streams` → `test_rapidgzip_deflate_zlib` in collection
  order. Other matrix legs green. Motivated the accelerator/PPMd-stream process split.

### Next steps

- Get a soak rate (even 1/20) under recipe (1), then A/B rapidgzip off (3).
- If rapidgzip-linked: try to shrink to a subprocess loop that only imports/uses
  rapidgzip the way the suite does (path vs `BytesIO`, truncated members, close vs GC),
  reusing ideas from `scripts/dual_accelerator_repro.py` / `tests/test_accelerator_shutdown.py`.
- If only the long mixed suite flakes: treat as CI hygiene (more process isolation, or
  coverage/accelerator policy on Linux) rather than a product API defect.
- Do **not** fold this into PPMd stress without adding a rapidgzip + long-suite axis;
  the existing PPMd job would stay green while this remains open.
