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
interpreter is finalizing. So the abort happens whenever a worker thread outlives its owning
Python object into interpreter shutdown.

What governs that, measured by the canary in `tests/test_accelerator_shutdown.py` (a matrix of
both accelerators × intact / corrupt / truncated input × closed / unclosed, each in its own
subprocess):

| Cleanup | Linux | Windows | macOS |
|---|---|---|---|
| **Unclosed** (finalized at interpreter shutdown) | abort | abort\* | abort |
| **Closed + `join_threads()`** during the run | clean | clean | **abort** |

\* Windows behaviour is recorded by the canary; the asserted columns are Linux/Windows = clean
and macOS = abort for the **closed** case.

The input variant (intact / corrupt / truncated) makes **no** difference — only cleanup and
platform do:

- On **Linux/Windows**, closing the stream and calling `join_threads()` during the run stops
  the worker thread, so shutdown is clean. archivey already closes accelerator streams
  deterministically (see `_AcceleratorStream` / `weakref.finalize` in
  `archivey.internal.streams.codecs`), so these platforms are unaffected in normal use.
- On **macOS**, the abort happens **even for a properly closed + joined stream**:
  `join_threads()` does not reliably stop the worker thread on the macOS builds. Nothing the
  library can do from Python prevents it.

### What was tried (and did not fix macOS)

1. `atexit` backstop closing any leaked stream — insufficient (close alone does not stop the
   thread on macOS).
2. `join_threads()` before `close()` on every stream — insufficient (`join_threads()` itself
   does not stop the thread on macOS).
3. `weakref.finalize` guaranteeing the join runs before the raw object is freed, even inside a
   reference cycle — insufficient (same reason).

All three are correct hygiene and are kept (they make Linux/Windows robust against the
*unclosed* case), but none addresses the macOS-specific failure.

### Workaround

`AcceleratorMode.AUTO` does **not** select an accelerator on macOS
(`_ACCELERATORS_UNSAFE_PLATFORM` in `archivey.internal.config`); gzip/bzip2 fall back to the
sequential stdlib backend (a slow rewinding seek, warned about, beats crashing). An explicit
`AcceleratorMode.ON` is still honoured — on macOS that carries the shutdown-abort risk, so the
in-process forced-`ON` tests are skipped there.

### Re-enabling (the canary)

`tests/test_accelerator_shutdown.py` asserts the current state: the **closed** case exits
cleanly off macOS and is **expected to abort on macOS**. If a future `rapidgzip` /
`indexed_bzip2` release makes the closed case exit cleanly on macOS, the macOS assertion
**fails** — that is the signal to revisit `_ACCELERATORS_UNSAFE_PLATFORM` and re-enable the
accelerators (per-accelerator, since the canary characterises each one separately).
