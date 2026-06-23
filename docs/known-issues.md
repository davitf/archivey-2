# Known issues

## Random-access accelerators abort the process at shutdown on macOS

**Status:** open (upstream); worked around by disabling the accelerators under `AUTO` on macOS.

### Symptom

With the `[seekable]` accelerators installed (`rapidgzip` for gzip, `indexed_bzip2` for
bzip2), a process that uses one of them aborts with **SIGABRT (exit code 134)** at
interpreter shutdown on macOS — *after* all work (and the whole test suite) has completed
successfully:

```
376 passed, 7 skipped
Detected Python finalization from running rapidgzip thread.
To avoid this exception you should close all RapidgzipFile objects correctly,
or better, use the with-statement if possible to automatically close it.
terminate called without an active exception
Process completed with exit code 134.
```

### Root cause

Both libraries spawn **C++ worker threads** (`std::thread`s, invisible to Python's
`threading` module — `threading.enumerate()` never lists them). Each library installs a guard
that calls `std::terminate()` if one of its worker threads is still running when the Python
interpreter is finalizing. So the abort happens whenever a worker thread outlives an explicit
join — i.e. whenever an accelerator object is **finalized without `join_threads()` having been
called on it while it was alive**.

What governs that, measured by the canary in `tests/test_accelerator_shutdown.py` (a matrix of
both accelerators × intact / corrupt / truncated input × cleanup strategy, each in its own
subprocess). The behaviour is **identical on Linux and macOS**, and the input variant makes
**no** difference — only how the object is finalized matters:

| Cleanup strategy | Linux | macOS |
|---|---|---|
| **closed** — `read()`, `join_threads()`, `close()` during the run | clean | clean |
| **cycle_gc** — object reclaimed by the cyclic GC mid-run (e.g. an exception traceback holds it in a cycle) | abort | abort |
| **unclosed** — object finalized at interpreter shutdown | abort | abort |

(Windows exit codes are recorded by the canary's warning but not yet asserted.)

The takeaway: **only an explicit, prompt `join_threads()` + `close()` is safe.** Leaving the
object to *any* finalizer — the cyclic garbage collector or interpreter shutdown — orphans the
worker thread and aborts the process, on every platform.

### Why the workaround is macOS-only

archivey closes accelerator streams deterministically — `_AcceleratorStream` joins on
`close()`, and a `weakref.finalize` guard joins on collection / at exit (see
`archivey.internal.streams.codecs`). On **Linux** and **Windows** that is enough: the full
test suite, which exercises the accelerators heavily (including corrupt/truncated reads that
raise), runs clean. On **macOS** the suite nonetheless aborted at shutdown — under its access
patterns and GC timing the deterministic-close guarantee did not hold for every stream, and we
could not make it reliable from Python. (`atexit`-close, `join_threads()`-on-close, and the
`weakref.finalize` guard were each tried; all are correct hygiene and are kept, but none made
the macOS suite reliable.)

Rather than ship a backend that can crash the process on one platform, AUTO disables the
accelerators there.

### Workaround

`AcceleratorMode.AUTO` does **not** select an accelerator on macOS
(`_ACCELERATORS_UNSAFE_PLATFORM` in `archivey.internal.config`); gzip/bzip2 fall back to the
sequential stdlib backend (a slow rewinding seek, warned about, beats crashing). An explicit
`AcceleratorMode.ON` is still honoured — on macOS that carries the shutdown-abort risk, so the
in-process forced-`ON` tests are skipped there.

### Re-enabling (the canary)

`tests/test_accelerator_shutdown.py` asserts the current state: the **closed** case exits
cleanly (the cleanup contract archivey depends on), and the **cycle_gc** and **unclosed**
cases — an object finalized without an explicit join — **abort**. If a future `rapidgzip` /
`indexed_bzip2` release no longer aborts when an object is finalized by the GC or at shutdown
(e.g. it joins its threads in the destructor, or makes them non-fatal at finalization), those
assertions **fail** — that is the signal that relying on the runtime to clean up is safe, and
to revisit `_ACCELERATORS_UNSAFE_PLATFORM` and re-enable the accelerators (per-accelerator,
since the canary characterises each one separately).
