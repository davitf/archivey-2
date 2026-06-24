# Known issues

## Random-access accelerators must be *closed* (not just joined) to avoid a shutdown abort

**Status:** mitigated. The stream wrapper now *closes* (not merely joins) the object on
finalization, which fixes every isolated case on Linux, Windows, **and macOS**. A residual
remains: the **full test suite on macOS** still aborts at shutdown once accelerators run
in-process, for a reason the isolated reproductions do not capture. Until that is understood,
`AUTO` keeps the accelerators disabled on macOS. A standalone reproduction to run on a real Mac
lives at `scripts/macos_accelerator_debug.py`.

### Symptom

With the `[seekable]` accelerators installed (`rapidgzip` for gzip, `indexed_bzip2` for
bzip2), a process that used one of them could abort with **SIGABRT (exit code 134)** at
interpreter shutdown — *after* all work had completed successfully:

```
Detected Python finalization from running rapidgzip thread.
To avoid this exception you should close all RapidgzipFile objects correctly,
or better, use the with-statement if possible to automatically close it.
terminate called without an active exception
Process completed with exit code 134.
```

### Root cause

Both libraries spawn **C++ worker threads** (`std::thread`s, invisible to Python's `threading`
module — `threading.enumerate()` never lists them). Each installs a guard that calls
`std::terminate()` if a worker thread is still running when the interpreter is finalizing.

The decisive detail: **`join_threads()` does not stop the worker thread — only `close()`
does.** (The libraries' own message says to "close all … objects".) So the abort happens
whenever an accelerator object is finalized **without having been closed**, regardless of
platform. Measured by the canary in `tests/test_accelerator_shutdown.py` (both accelerators ×
intact / corrupt / truncated input × cleanup strategy, each in its own subprocess); the
behaviour is identical on Linux and macOS, and the input variant makes no difference:

| Cleanup strategy | Result |
|---|---|
| **closed** — `read()`, then `join_threads()` + `close()` during the run | clean |
| **raw cycle_gc** — raw object reclaimed by the cyclic GC mid-run (e.g. an exception traceback holds it in a cycle), never closed | **abort** |
| **raw unclosed** — raw object finalized at interpreter shutdown, never closed | **abort** |
| **guarded cycle_gc / unclosed** — same two paths, but a `weakref.finalize` guard **closes** the object on finalization | clean |

(Windows exit codes are recorded by the canary's warning but not asserted.)

The takeaway: an accelerator object must be **closed** before it is freed. Merely joining its
threads is not enough; leaving it to a finalizer that only joins (or does nothing) aborts the
process on every platform.

### How archivey handles it

`_AcceleratorStream` (in `archivey.internal.streams.codecs`) wraps every accelerator object and
installs a `weakref.finalize` guard that **closes** the raw object exactly once — when the
wrapper is collected (cyclically or not) or at interpreter exit, whichever comes first — holding
a strong reference to the raw object so the close always runs before it is freed. `close()` on
the wrapper just triggers the same guard early.

This makes leaked, cyclically-collected, and never-closed streams all shut down cleanly in
isolation on **every** platform, including macOS (the canary's `guard_*` cases pass on darwin).

> Earlier the guard called `join_threads()` instead of `close()`. That is insufficient (see the
> table), which explains much of the macOS abort: streams that reached the join-only guard rather
> than an explicit `close()` were never actually stopped. Switching the guard to `close()` fixed
> every isolated reproduction.

### The unresolved residual (macOS)

Even with the close-on-finalize guard, the **full pytest suite on macOS** still exits 134 *after*
all tests pass, once accelerators are active in-process (forced-`ON` tests plus `AUTO`). The
isolated subprocess canary does **not** reproduce this — its `arch_*` scenarios are all clean on
macOS — so something about the full-process environment (many streams, retained tracebacks, or
the `pytest-cov` `sys.settrace` C tracer shifting finalization timing) is the trigger.

Until this is root-caused, `AUTO` does not select an accelerator on macOS
(`_ACCELERATORS_UNSAFE_PLATFORM` in `archivey.internal.config`); gzip/bzip2 fall back to the
sequential stdlib backend (a slow rewinding seek, warned about, beats crashing). An explicit
`AcceleratorMode.ON` is still honoured, and the in-process forced-`ON` tests are skipped on macOS.

`scripts/macos_accelerator_debug.py` is a self-contained reproduction to run on a real Mac: it
prints the environment and runs a matrix (raw vs. archivey-wrapped × close / leaked-to-shutdown /
cyclic-GC / exception-cycle / seek / many-streams), each in its own subprocess, with and without a
tracer (`--trace`) and optionally under real `coverage`. Whichever `arch_*` scenario aborts there
— especially if only under the tracer — is the minimal reproduction we need.

### The canary

`tests/test_accelerator_shutdown.py` asserts the contract: the **closed** case and the two
**guarded** finalization paths exit cleanly on every platform (if they ever abort, archivey's
own cleanup is broken), while the **raw** `cycle_gc` / `unclosed` paths abort (the upstream
quirk). If a future `rapidgzip` / `indexed_bzip2` release stops aborting on a raw,
never-closed object (e.g. it closes/joins in its destructor), the raw-case assertions **fail** —
the signal that the close-on-finalize guard is no longer load-bearing and the wrapper could be
simplified.
