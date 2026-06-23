"""Canary for the accelerator interpreter-shutdown abort and for archivey's guard against it
(see ``docs/known-issues.md`` and ``_AcceleratorStream`` in ``archivey.internal.streams.codecs``).

``rapidgzip`` and ``indexed_bzip2`` spawn C++ worker threads. A thread still running when the
interpreter finalizes trips their guard ("Detected Python finalization from running … thread")
and aborts the process with SIGABRT. Critically, ``join_threads()`` does **not** stop the
thread — only ``close()`` does — so a stream must be *closed*, not merely joined, before it is
freed. Each scenario runs in its own subprocess so the abort is contained (the child crashes;
this parent test inspects the exit code).

The matrix crosses both accelerators × intact / corrupt / truncated input × these cleanup
strategies, and is emitted as a warning for the record:

- ``closed`` — ``read()`` then ``join_threads()`` + ``close()`` during the run.
- ``cycle_gc`` / ``unclosed`` — the **raw** object finalized by the cyclic GC mid-run, or left
  to interpreter shutdown, with **no** close. These abort on every platform measured — the
  underlying-library bug archivey works around.
- ``guard_cycle_gc`` / ``guard_unclosed`` — the same two finalization paths but wrapped in a
  faithful copy of archivey's ``weakref.finalize`` guard, which **closes** the raw object when
  the wrapper is reclaimed (cyclically or at exit). These exit cleanly, which is why the
  accelerators are safe to use on every platform.

Asserted invariants (see the test): ``closed`` and the two ``guard_*`` paths exit cleanly on
every platform (the cleanup contract archivey depends on), while the raw ``cycle_gc`` /
``unclosed`` paths abort (the upstream bug; if a future release stops aborting there, the
assertion flips, signalling the close-on-finalize guard is no longer load-bearing).
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import warnings

import pytest

# (module, builder-format) for the two accelerators.
_ACCELERATORS = [("rapidgzip", "gzip"), ("indexed_bzip2", "bz2")]
_VARIANTS = ["intact", "corrupt", "truncated"]
# Cleanup strategies, in increasing "let the runtime do it" order:
#   closed         — read, join_threads(), close() during the run (what archivey does).
#   cycle_gc       — raw object dropped into a reference cycle and reclaimed by the cyclic GC
#                    mid-run (the mechanism a corrupt/truncated read's exception traceback
#                    creates), with no join.
#   guard_cycle_gc — same, but wrapped in archivey's weakref.finalize join guard.
#   unclosed       — raw object finalized at interpreter shutdown, with no join.
#   guard_unclosed — same, but wrapped in archivey's weakref.finalize join guard.
_CLEANUPS = ["closed", "cycle_gc", "guard_cycle_gc", "unclosed", "guard_unclosed"]


def _script(module: str, fmt: str, variant: str, cleanup: str) -> str:
    """A minimal standalone program that uses the accelerator *directly* (no archivey)."""
    return textwrap.dedent(
        f"""
        import gc, io, gzip, bz2, weakref
        import {module}

        payload = b'canary payload ' * 4000
        data = bytearray(gzip.compress(payload) if {fmt!r} == 'gzip' else bz2.compress(payload))
        if {variant!r} == 'corrupt':
            data[15:40] = b'\\x00' * 25
        elif {variant!r} == 'truncated':
            data = data[: len(data) // 2]

        cleanup = {cleanup!r}

        # A faithful copy of archivey's _AcceleratorStream guard: weakref.finalize holds the
        # raw object strongly and joins its threads exactly once, when the wrapper is collected
        # (cyclically or not) or at interpreter exit — whichever comes first.
        def _close(inner):
            jt = getattr(inner, 'join_threads', None)
            if jt is not None:
                try:
                    jt()
                except Exception:
                    pass
            try:
                inner.close()  # join_threads() alone does not stop the thread; close() does
            except Exception:
                pass

        class Guard:
            def __init__(self, inner):
                self._inner = inner
                self._fin = weakref.finalize(self, _close, inner)

        raw = {module}.open(io.BytesIO(bytes(data)), parallelization=0)
        if cleanup.startswith('guard_'):
            obj = Guard(raw)
            del raw            # only the guard (and its finalize) reference the raw object
            reader = obj._inner
        else:
            obj = raw
            reader = raw
        try:
            reader.read()
        except Exception:
            pass  # corrupt/truncated reads may raise; shutdown behaviour is what we measure
        del reader

        if cleanup == 'closed':
            try:
                obj.join_threads()
            except Exception:
                pass
            obj.close()
        elif cleanup in ('cycle_gc', 'guard_cycle_gc'):
            # Make `obj` reachable only through a reference cycle, then reclaim it via the
            # cyclic collector during the run (not at shutdown). For the raw object this
            # detaches the worker thread; for the guarded object the finalize must still join.
            box = []
            box.append(box)
            box.append(obj)
            del obj
            gc.collect()
        # 'unclosed' / 'guard_unclosed': leave `obj` referenced so it is finalized at shutdown.
        """
    )


def _run(module: str, fmt: str, variant: str, cleanup: str) -> int:
    """Run one scenario in a subprocess; return its exit code (negative == killed by signal)."""
    proc = subprocess.run(
        [sys.executable, "-c", _script(module, fmt, variant, cleanup)],
        capture_output=True,
        timeout=30,
    )
    return proc.returncode


@pytest.mark.parametrize(("module", "fmt"), _ACCELERATORS, ids=[m for m, _ in _ACCELERATORS])
def test_accelerator_shutdown_canary(module: str, fmt: str) -> None:
    pytest.importorskip(module)

    matrix = {
        f"{variant}/{cleanup}": _run(module, fmt, variant, cleanup)
        for variant in _VARIANTS
        for cleanup in _CLEANUPS
    }
    warnings.warn(
        f"[accel-shutdown] {module} (platform={sys.platform}) exit codes: {matrix}",
        stacklevel=1,
    )

    # The measured behaviour is the same on every platform (the input variant is irrelevant);
    # only how the object is finalized matters.

    # 1. The cleanup contract archivey depends on: an accelerator object that is closed exits
    #    cleanly — whether close() is called explicitly during the run ('closed') or by the
    #    weakref.finalize guard when the wrapper is reclaimed by the cyclic GC ('guard_cycle_gc')
    #    or at interpreter exit ('guard_unclosed'). The guard is a faithful copy of
    #    _AcceleratorStream's, so if any of these abort, archivey's own cleanup is broken.
    for variant in _VARIANTS:
        for safe in ("closed", "guard_cycle_gc", "guard_unclosed"):
            rc = matrix[f"{variant}/{safe}"]
            assert rc == 0, (
                f"{module}: a {variant}/{safe} stream aborted on {sys.platform} (rc={rc}) — "
                f"the accelerator cleanup contract is broken (close() should stop the thread)."
            )

    # 2. The underlying-library bug, and the canary for it: a raw accelerator object finalized
    #    *without* being closed — reclaimed by the cyclic GC ('cycle_gc', the exception-traceback
    #    cycle path) or left to interpreter shutdown ('unclosed') — aborts with SIGABRT, because
    #    join_threads() alone does not stop the C++ worker thread; only close() does. This is the
    #    whole reason _AcceleratorStream's guard must close (not merely join) the object. When a
    #    future accelerator release stops aborting here (e.g. it closes/joins in its destructor),
    #    these assertions fail — the signal that the guard is no longer load-bearing.
    #
    #    Asserted on the characterised platforms (Linux, macOS); Windows codes are recorded in
    #    the warning above but not asserted.
    if sys.platform in ("linux", "darwin"):
        for ungraceful in ("cycle_gc", "unclosed"):
            rc = matrix[f"intact/{ungraceful}"]
            assert rc != 0, (
                f"{module}: a raw (unguarded) {ungraceful} object now exits cleanly on "
                f"{sys.platform} (rc={rc}). The upstream interpreter-finalization abort may be "
                f"fixed — _AcceleratorStream's close-on-finalize guard may no longer be needed."
            )
