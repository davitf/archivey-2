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

from archivey.internal.config import _ACCELERATORS_UNSAFE_PLATFORM

# (module, builder-format) for the two accelerators.
_ACCELERATORS = [("rapidgzip", "gzip"), ("indexed_bzip2", "bz2")]
_VARIANTS = ["intact", "corrupt", "truncated"]
_CLEANUPS = ["closed", "unclosed"]


def _script(module: str, fmt: str, variant: str, cleanup: str) -> str:
    """A minimal standalone program that uses the accelerator *directly* (no archivey)."""
    return textwrap.dedent(
        f"""
        import io, gzip, bz2
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

    # The asserted canary: a properly closed + joined stream. Use the intact input (the
    # corrupt/truncated columns are in the warning above for the record).
    closed_rc = matrix["intact/closed"]
    if _ACCELERATORS_UNSAFE_PLATFORM:
        assert closed_rc != 0, (
            f"{module}: a closed+joined accelerator stream now exits cleanly on macOS "
            f"(rc={closed_rc}). The upstream interpreter-shutdown abort may be fixed — "
            f"revisit _ACCELERATORS_UNSAFE_PLATFORM and consider re-enabling accelerators."
        )
    else:
        assert closed_rc == 0, (
            f"{module}: a closed+joined accelerator stream aborted at shutdown on "
            f"{sys.platform} (rc={closed_rc}), a platform archivey treats as safe."
        )
