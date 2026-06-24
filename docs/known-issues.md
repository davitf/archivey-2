# Known issues

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
