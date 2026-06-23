"""Canary for the accelerator interpreter-shutdown abort (the reason ``AUTO`` does not
select ``rapidgzip`` / ``indexed_bzip2`` on macOS — see ``docs/known-issues.md``).

``rapidgzip`` and ``indexed_bzip2`` spawn C++ worker threads. A thread still running when the
interpreter finalizes trips their guard ("Detected Python finalization from running … thread")
and aborts the process with SIGABRT. Two things determine whether that happens:

- **Cleanup**: an object left unclosed until interpreter shutdown aborts on *every* platform
  (its thread is alive at finalization). Closing + ``join_threads()`` during the run avoids
  that — on Linux and Windows. This is why archivey closes accelerator streams deterministically.
- **Platform**: on macOS the abort happens **even for a properly closed + joined stream**
  (``join_threads()`` does not reliably stop the worker there), which is why archivey disables
  the accelerators under ``AUTO`` on macOS.

Each scenario runs in its own subprocess so the abort is contained (the child crashes; this
parent test inspects the exit code). The full matrix — both accelerators × intact / corrupt /
truncated input × closed / unclosed — is emitted as a warning for the record. The asserted
canary is the **closed** case: it must exit cleanly on platforms we treat as safe, and is
expected to still abort on macOS. If a future accelerator release makes the closed case exit
cleanly on macOS, the macOS assertion fails — the signal to revisit and re-enable accelerators
there (see ``_ACCELERATORS_UNSAFE_PLATFORM`` in ``archivey.internal.config``).
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
#   closed   — read, join_threads(), close() during the run (what archivey does).
#   cycle_gc — drop the object into a reference cycle and reclaim it via the cyclic GC
#              *mid-run* (the mechanism a corrupt/truncated read's exception traceback would
#              create), to see whether cyclic finalization detaches the worker thread.
#   unclosed — keep the object referenced so it is finalized at interpreter shutdown.
_CLEANUPS = ["closed", "cycle_gc", "unclosed"]


def _script(module: str, fmt: str, variant: str, cleanup: str) -> str:
    """A minimal standalone program that uses the accelerator *directly* (no archivey)."""
    return textwrap.dedent(
        f"""
        import gc, io, gzip, bz2
        import {module}

        payload = b'canary payload ' * 4000
        data = bytearray(gzip.compress(payload) if {fmt!r} == 'gzip' else bz2.compress(payload))
        if {variant!r} == 'corrupt':
            data[15:40] = b'\\x00' * 25
        elif {variant!r} == 'truncated':
            data = data[: len(data) // 2]

        f = {module}.open(io.BytesIO(bytes(data)), parallelization=0)
        try:
            f.read()
        except Exception:
            pass  # corrupt/truncated reads may raise; shutdown behaviour is what we measure

        if {cleanup!r} == 'closed':
            try:
                f.join_threads()
            except Exception:
                pass
            f.close()
        elif {cleanup!r} == 'cycle_gc':
            # Make `f` reachable only through a reference cycle, then reclaim it via the
            # cyclic collector during the run (not at shutdown). If cyclic finalization runs
            # the C++ destructor without joining, the worker thread is detached and survives.
            box = []
            box.append(box)
            box.append(f)
            del f
            gc.collect()
        # 'unclosed': leave `f` referenced so it is finalized at interpreter shutdown.
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

    # The measured behaviour (identical on Linux and macOS): the variant (intact / corrupt /
    # truncated) is irrelevant — only how the object is finalized matters.

    # 1. Cleanup contract: a stream closed with join_threads() during the run exits cleanly
    #    on every platform. archivey relies on this; if it ever breaks, our close path is wrong.
    for variant in _VARIANTS:
        rc = matrix[f"{variant}/closed"]
        assert rc == 0, (
            f"{module}: a closed+joined {variant} stream aborted at shutdown on "
            f"{sys.platform} (rc={rc}) — the accelerator cleanup contract is broken."
        )

    # 2. The bug (the re-enable signal): an accelerator object finalized *without* an explicit
    #    join — left to interpreter shutdown ('unclosed') or reclaimed by the cyclic GC
    #    ('cycle_gc', the exception-traceback-cycle path) — aborts the process with SIGABRT.
    #    This is why archivey must close accelerator streams deterministically and why AUTO
    #    does not select them on macOS (where the suite's access patterns hit this). When a
    #    future accelerator release no longer aborts here, these assertions fail — the signal
    #    to revisit _ACCELERATORS_UNSAFE_PLATFORM and re-enable accelerators.
    #
    #    Asserted only on platforms whose behaviour is characterised here (Linux, macOS);
    #    Windows exit codes are recorded in the warning above but not asserted yet.
    if sys.platform in ("linux", "darwin"):
        for ungraceful in ("unclosed", "cycle_gc"):
            rc = matrix[f"intact/{ungraceful}"]
            assert rc != 0, (
                f"{module}: an {ungraceful} accelerator object now exits cleanly on "
                f"{sys.platform} (rc={rc}). The upstream interpreter-finalization abort may "
                f"be fixed — revisit _ACCELERATORS_UNSAFE_PLATFORM and consider re-enabling."
            )
