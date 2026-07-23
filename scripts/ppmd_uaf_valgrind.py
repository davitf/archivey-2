#!/usr/bin/env python3
"""Deterministic valgrind gate for the pyppmd output-buffer use-after-free.

Background
----------
On pyppmd 1.3.x the native decode worker thread is left blocked in its input
reader whenever a ``decode`` requests more output than the fed input can produce
(``max_length=-1``, an oversized bound, or a large post-eof / NUL budget over a
truncated stream). ``OutputBuffer_Finish`` then frees the decode's output block
while that worker still holds a raw pointer into it, and the worker is later
resumed (by the next call or by ``Ppmd7T_Free`` at teardown) to free-run into the
freed block — a use-after-free write at ``ThreadDecoder.c:134``. Root-cause
analysis, valgrind evidence, and the 1.2.0→1.3.0 (#126) regression window are in
``docs/internal/ppmd-native-investigation-results.md`` (§D, §J).

Unlike the crash-rate soak in ``scripts/pyppmd_crash_repro.py`` (a probabilistic
race whose rate swings with allocator layout and Python/GIL mode), this driver is
**deterministic**: valgrind's memcheck reports the invalid write on the shape that
reproduces it every run. It runs in the non-required ``PPMd native stress``
workflow (Linux). A green run here on a fixed pyppmd — or archivey's
``quiesce-on-close`` mitigation for the archivey scenarios — across the hot-race
platforms is the evidence needed to retire ``--allow-exit-after-green`` for the
PPMd module; it is **not** on its own sufficient on a single host (the observable
teardown abort is already ~0 there).

Scenarios
---------
* ``pyppmd-overshoot`` — bare ``pyppmd``: ``decode(packed, -1)`` over a truncated
  stream. The canonical reproducer; **expected to fail (nonzero) on 1.3.x**, pass
  on a fixed release. Use it as a pyppmd-regression / fixed-release detector.
* ``archivey-truncated`` — archivey ``PpmdDecoder``: truncated PPMd7 fed + flushed
  then disposed via ``__del__``. Exercises the shipped mitigations + the
  ``quiesce-on-close`` fix; expected **clean**. A failure here is an archivey bug.
* ``archivey-stream`` — same, driven through ``PpmdDecompressorStream`` so disposal
  runs the explicit ``close()`` path. Expected **clean**.

Usage
-----
::

    python scripts/ppmd_uaf_valgrind.py                       # all archivey scenarios
    python scripts/ppmd_uaf_valgrind.py --scenario archivey-truncated
    python scripts/ppmd_uaf_valgrind.py --scenario pyppmd-overshoot  # pyppmd detector
    python scripts/ppmd_uaf_valgrind.py --scenario all --cycles 8

Exit code is nonzero if valgrind reports any memcheck error in a scenario that is
expected to be clean (the ``pyppmd-overshoot`` detector is reported but does not
fail the run unless ``--strict-pyppmd`` is passed). Requires ``valgrind`` on PATH.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Highly compressible payload: a small mid-stream cut still leaves the worker
# wanting many more symbols than the truncated input can produce, so it parks past
# logical EOF — the precondition for the UAF.
_PREAMBLE = textwrap.dedent(
    """\
    import os, sys
    sys.path.insert(0, os.path.join({repo!r}, "src"))
    sys.path.insert(0, {repo!r})
    import pyppmd

    ORDER, MEM = 6, 1 << 20
    CONTENT = (b"the quick brown fox jumps over the lazy dog\\n" * 200)
    _e = pyppmd.Ppmd7Encoder(ORDER, MEM)
    PACKED = _e.encode(CONTENT) + _e.flush()
    CYCLES = int(os.environ.get("PPMD_UAF_CYCLES", "6"))
    """
)

_SCENARIOS: dict[str, str] = {
    # Bare pyppmd, the canonical reproducer (fails on 1.3.x, passes when fixed).
    "pyppmd-overshoot": _PREAMBLE
    + textwrap.dedent(
        """\
        for _ in range(CYCLES):
            dec = pyppmd.Ppmd7Decoder(ORDER, MEM)
            out = dec.decode(PACKED[: len(PACKED) // 2], -1)  # INT_MAX budget, truncated
            del dec
        print("ok", flush=True)
        """
    ),
    # archivey PpmdDecoder, truncated PPMd7, disposed via __del__.
    "archivey-truncated": _PREAMBLE
    + textwrap.dedent(
        """\
        from archivey.internal.streams.decompress import PpmdDecoder
        for _ in range(CYCLES):
            dec = PpmdDecoder(order=ORDER, mem_size=MEM, variant=7,
                              unpack_size=len(CONTENT), pack_size=len(PACKED))
            dec.feed(PACKED[: len(PACKED) // 2], len(CONTENT))
            try:
                dec.flush()
            except Exception:
                pass
            del dec
        print("ok", flush=True)
        """
    ),
    # archivey stream, disposal via the explicit close() path (context manager).
    "archivey-stream": _PREAMBLE
    + textwrap.dedent(
        """\
        import io
        from archivey.exceptions import TruncatedError
        from archivey.internal.streams.decompress import PpmdDecompressorStream
        for _ in range(CYCLES):
            with PpmdDecompressorStream(
                io.BytesIO(PACKED[: len(PACKED) // 2]),
                order=ORDER, mem_size=MEM, variant=7,
                unpack_size=len(CONTENT), pack_size=len(PACKED),
            ) as stream:
                try:
                    stream.read()
                except TruncatedError:
                    pass
        print("ok", flush=True)
        """
    ),
}

# Scenarios expected to be clean (a memcheck error fails the run). The bare-pyppmd
# reproducer is a detector: on 1.3.x it is *expected* to error, so it does not fail
# the run unless --strict-pyppmd is given.
_EXPECT_CLEAN = {"archivey-truncated", "archivey-stream"}

_ERROR_SUMMARY = re.compile(r"ERROR SUMMARY:\s+(\d+)\s+errors")


def _run_scenario(name: str, cycles: int, timeout: float) -> tuple[int, bool, str]:
    """Return (memcheck_error_count, body_ok, tail_of_output)."""
    script = _SCENARIOS[name]
    env = dict(os.environ)
    env["PPMD_UAF_CYCLES"] = str(cycles)
    # Keep pymalloc (do NOT set PYTHONMALLOC=malloc): the UAF'd output blocks are
    # large PyBytes that pymalloc routes to raw malloc, so valgrind already tracks
    # them; forcing libc malloc for every allocation instead floods the report with
    # CPython-interpreter false positives (measured ~984 errors on a clean run).
    with tempfile.TemporaryDirectory(prefix="ppmd-uaf-") as tmp:
        driver = Path(tmp) / f"{name}.py"
        driver.write_text(script.format(repo=str(_REPO_ROOT)), encoding="utf-8")
        cmd = [
            "valgrind",
            "--error-exitcode=0",  # we parse ERROR SUMMARY ourselves
            "--num-callers=6",
            "--smc-check=all",
            sys.executable,
            str(driver),
        ]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
            check=False,
        )
    combined = (proc.stdout or "") + (proc.stderr or "")
    body_ok = any(line.strip() == "ok" for line in (proc.stdout or "").splitlines())
    match = _ERROR_SUMMARY.search(combined)
    errors = int(match.group(1)) if match else -1
    tail = "\n".join(combined.strip().splitlines()[-8:])
    return errors, body_ok, tail


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--scenario",
        choices=[*sorted(_SCENARIOS), "all", "archivey"],
        default="archivey",
        help="Scenario to run ('archivey' = both archivey scenarios; 'all' = every scenario).",
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=6,
        help="Decode/dispose cycles per child (default 6).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=400.0,
        help="Per-scenario valgrind timeout (s).",
    )
    parser.add_argument(
        "--strict-pyppmd",
        action="store_true",
        help="Also fail the run if the bare-pyppmd reproducer reports errors (i.e. require a fixed pyppmd).",
    )
    args = parser.parse_args(argv)

    if shutil.which("valgrind") is None:
        print("valgrind not found on PATH (apt-get install valgrind)", file=sys.stderr)
        return 2
    try:
        import pyppmd  # noqa: F401
    except ImportError:
        print("pyppmd is not installed (pip install 'pyppmd>=1.3.1')", file=sys.stderr)
        return 2

    if args.scenario == "all":
        names = sorted(_SCENARIOS)
    elif args.scenario == "archivey":
        names = ["archivey-truncated", "archivey-stream"]
    else:
        names = [args.scenario]

    failed = False
    print(f"ppmd_uaf_valgrind: scenarios={names} cycles={args.cycles}")
    for name in names:
        errors, body_ok, tail = _run_scenario(name, args.cycles, args.timeout)
        # `pyppmd-overshoot` is a detector: memcheck errors are the *expected*
        # outcome on 1.3.x and do not fail the run unless --strict-pyppmd requires
        # a fixed pyppmd. Every other scenario must be clean.
        clean_required = name in _EXPECT_CLEAN or (
            name == "pyppmd-overshoot" and args.strict_pyppmd
        )
        # A scenario that never ran its body (import error, early crash, wrong env)
        # or produced no parseable valgrind summary is a failure regardless: a
        # "0 errors" from a body that never executed the repro is a false PASS.
        if not body_ok:
            status, scenario_failed = "FAIL (body did not run)", True
        elif errors < 0:
            status, scenario_failed = "FAIL (no valgrind summary)", True
        elif errors == 0:
            status, scenario_failed = "OK", False
        elif clean_required:
            status, scenario_failed = "FAIL", True
        else:
            status, scenario_failed = "DETECTED", False  # detector, expected
        failed = failed or scenario_failed
        print(f"  {name:<20} memcheck_errors={errors:<6} {status}")
        if scenario_failed or errors != 0:
            print(textwrap.indent(tail, "      "))

    print()
    print("PASS" if not failed else "FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
