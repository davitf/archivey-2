# Known issues

> See also [Compression-library analysis](library-analysis.md) for which library backs each
> codec and why — including why `rapidgzip` is the single accelerator library (the issue below)
> and why an `indexed_zstd` zstd accelerator would face the same constraint.

## stdlib `tarfile` treats a corrupt non-first header as clean end-of-archive

`tarfile.TarFile.next()` re-raises `InvalidHeaderError` only when it occurs at offset 0;
a corrupt member header anywhere later is swallowed and iteration simply ends — so
mid-archive corruption produces a **silently shortened listing**, never a
`CorruptionError`. Confirmed against a corrupted-checksum fixture and a corrupted
`.tar.gz` whose garbage decode parses as an invalid header (deep review W1). Archivey's
backstop is the end-of-archive marker check in `TarReader._verify_tar_eof`: a
corruption-shortened listing almost never sits on a valid two-block null trailer, so it
fires `ARCHIVE_EOF_MARKER_MISSING` (WARNING by default; `strict_archive_eof=True`
escalates to `TruncatedError`). The diagnostic message names both possibilities. A
native TAR header walker (the 7z/RAR strategy applied to TAR) would make this
archivey's own decision instead of tarfile's; until then the leniency is documented in
`docs/formats.md`.

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

**Status: pinned to PPMd/`pyppmd`; root cause not fixed; Linux and Windows both affected
(different trigger shapes).** Not adversarial input — happy-path encode/decode of valid
PPMd data.

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

Repro (non-blocking stress entry points):

```bash
uv run --no-sync python scripts/ppmd_native_stress.py 30 --scenarios warmup_codecs
uv run --no-sync pytest -m ppmd_native_stress -k warmup --timeout=600 -o addopts=
```

### Mitigation in the required CI matrix

- **Skip** the `ppmd` parametrization of `test_py7zr_codec_fixtures_roundtrip` on `win32`
  (still runs on Linux/macOS).
- Other Windows codec labels keep per-label subprocess isolation.
- Default pytest excludes `-m 'not ppmd_native_stress'` so stress tests never fail the
  required suite.
- Deterministic **raw** PPMd coverage (no 7z) lives in `tests/test_ppmd_raw_streams.py` and
  stays in the required suite — those paths have been stable in short soaks.
- **Product hardening (related, not a proven crash fix):** archivey now passes the 7z
  folder unpack size into PPMd as `max_length` (and ZIP member size for PPMd8), and on
  flush feeds the pyppmd “extra NUL” only within the remaining length — matching py7zr /
  the [pyppmd PyPI note](https://pypi.org/project/pyppmd/). Previously `decode(..., -1)`
  could overshoot the true payload on PPMd7 (no end mark); that is a correctness issue and
  a plausible native-stress contributor, but the intermittent abort after other-codec
  warmup is still open.

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

### Next leads

- Prefer `raw_*` crash-rate deltas to decide whether the Windows abort is inherent to
  `pyppmd` vs archivey’s stream wrapper vs the 7z path.
- On Linux, compare `warmup_codecs` vs `fresh_baseline` vs `raw_*` (so far only warmup is
  hot) — if another agent’s Linux native crashes match warmup/SIGSEGV, fold them here;
  if they are raw-`pyppmd`-only, escalate as a broader upstream lead.
- Possible archivey-side experiments (unconfirmed): decoder lifecycle/close, smaller
  `skip_forward` chunks, `stream_members()` vs repeated `read()`.
