#!/usr/bin/env python3
"""Find which accelerator streams reach interpreter shutdown un-closed on macOS.

This is the diagnostic for the *residual* macOS abort: the full pytest suite exits 134 at
shutdown even though every isolated reproduction is clean. It instruments archivey's
``_AcceleratorStream`` to record the creation stack of every accelerator stream, then prints —
just before interpreter finalization — every stream that is still alive and was **never
explicitly closed** (i.e. is relying on the ``weakref.finalize`` guard). Those are the streams
whose C++ worker thread can still be running when the interpreter finalizes.

It also:
  * force-enables the accelerators on macOS for this run (defeats the ``AUTO`` disable and the
    in-process test skips), so the suite actually exercises them; and
  * enables ``faulthandler`` so the SIGABRT dumps Python thread stacks at the crash.

Run it INSTEAD of pytest — it takes pytest arguments and runs the suite in-process after
installing the instrumentation:

    # matches CI (coverage on via pyproject addopts) — most likely to reproduce:
    PYTHONFAULTHANDLER=1 uv run python scripts/accel_leak_trace.py tests/

    # same, but with coverage OFF, to test whether pytest-cov's tracer is the trigger:
    PYTHONFAULTHANDLER=1 uv run python scripts/accel_leak_trace.py tests/ -p no:cov

    # narrow to the accelerator tests once you see which ones leak:
    PYTHONFAULTHANDLER=1 uv run python scripts/accel_leak_trace.py tests/test_accelerator_corruption.py

Read the "[accel-leak-trace]" report at the very end of the output (it prints right before the
abort). Each distinct creation stack is shown once with a count; the top frame inside a test is
the code path that opened a stream without closing it. Please paste that report into the thread.
"""

from __future__ import annotations

import atexit
import collections
import faulthandler
import sys
import traceback
import weakref

faulthandler.enable()

from archivey.internal import config as _config  # noqa: E402
from archivey.internal.streams import codecs as _codecs  # noqa: E402

# Force-enable the accelerators on macOS for this run. The test skips and AUTO gate both read
# this flag, so setting it before pytest collects makes the suite exercise the accelerators.
_config._ACCELERATORS_UNSAFE_PLATFORM = False

# id(wrapper) -> {"stack": str, "ref": weakref, "closed": bool}
_RECORDS: dict[int, dict[str, object]] = {}

_orig_init = _codecs._AcceleratorStream.__init__
_orig_close = _codecs._AcceleratorStream.close


def _traced_init(self, inner):  # noqa: ANN001
    _orig_init(self, inner)
    _RECORDS[id(self)] = {
        "stack": "".join(traceback.format_stack(limit=15)),
        "ref": weakref.ref(self),
        "closed": False,
    }


def _traced_close(self):  # noqa: ANN001
    rec = _RECORDS.get(id(self))
    if rec is not None:
        rec["closed"] = True
    _orig_close(self)


_codecs._AcceleratorStream.__init__ = _traced_init
_codecs._AcceleratorStream.close = _traced_close


@atexit.register
def _report() -> None:
    # Registered after weakref's own atexit hook, so (LIFO) this runs first — before the guard
    # closes anything — and reflects what the test code itself left open.
    leaked = [
        rec
        for rec in _RECORDS.values()
        if not rec["closed"] and rec["ref"]() is not None  # type: ignore[operator]
    ]
    out = sys.stderr
    out.write("\n" + "=" * 90 + "\n")
    out.write(
        f"[accel-leak-trace] {len(_RECORDS)} accelerator stream(s) created this run; "
        f"{len(leaked)} still alive and NOT explicitly closed at interpreter shutdown.\n"
    )
    if not leaked:
        out.write(
            "[accel-leak-trace] No un-closed streams — the abort (if any) is elsewhere.\n"
        )
    else:
        out.write(
            "[accel-leak-trace] These rely on the finalize guard; their worker threads "
            "may still be running at finalization. Grouped by creation stack:\n"
        )
        by_stack = collections.Counter(rec["stack"] for rec in leaked)  # type: ignore[arg-type]
        for i, (stack, count) in enumerate(by_stack.most_common(), 1):
            out.write(
                f"\n--- leaked stream group #{i}  (x{count}) created at ---\n{stack}"
            )
    out.write("=" * 90 + "\n")
    out.flush()


def main() -> int:
    args = sys.argv[1:]
    if not args:
        args = ["tests/"]
    import pytest

    return pytest.main(args)


if __name__ == "__main__":
    sys.exit(main())
