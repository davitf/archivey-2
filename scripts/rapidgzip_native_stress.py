#!/usr/bin/env python3
"""Stress-test rapidgzip native surfaces (gzip + bundled IndexedBzip2File).

Background
----------
``rapidgzip`` is archivey's sole random-access accelerator for gzip *and* bzip2
(bundled ``IndexedBzip2File`` — the standalone ``indexed_bzip2`` package is never
imported). Known issues (see ``docs/internal/known-issues.md``):

- Worker threads abort the process if not ``close()``-d before finalization
  (SIGABRT; covered by ``tests/test_accelerator_shutdown.py``).
- Leading suspect for intermittent Linux full-suite heap corruption under ``[all]``.

This script is the dedicated investigation vehicle, mirroring
``scripts/ppmd_native_stress.py``: each iteration is a **fresh child process**.

Default scenarios::

    uv run --extra all python scripts/rapidgzip_native_stress.py
    uv run --extra all python scripts/rapidgzip_native_stress.py 40
    uv run --extra all python scripts/rapidgzip_native_stress.py --scenarios \
        archivey_gzip_bytesio many_cycles_path

Exit code is non-zero if any child crashed or failed.
"""

from __future__ import annotations

import argparse
import os
import sys
import textwrap
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from native_stress_common import (  # noqa: E402
    phase_helpers,
    run_stress_matrix,
    safe_print,
)

_WORK_ENV = "ARCHIVEY_RAPIDGZIP_STRESS_WORK"

_DEFAULT_SCENARIOS: tuple[str, ...] = (
    "raw_gzip_path_close",
    "raw_gzip_bytesio_close",
    "raw_bzip2_bytesio_close",
    "archivey_gzip_bytesio",
    "archivey_bzip2_bytesio",
    "many_cycles_path",
    "truncated_gzip_close",
)

_ALL_SCENARIOS: tuple[str, ...] = (
    *_DEFAULT_SCENARIOS,
    "guard_cycle_gc",  # expects clean exit (archivey finalize guard)
)


def _write_driver(scenario: str, seed: int) -> str:
    body = phase_helpers(work_env=_WORK_ENV)
    if scenario == "raw_gzip_path_close":
        body += textwrap.dedent(
            """\
            import gzip
            from pathlib import Path
            import rapidgzip
            payload = b"rapidgzip-stress-line\\n" * 2000
            path = work / "payload.gz"
            path.write_bytes(gzip.compress(payload))
            _phase(f"open_path size={path.stat().st_size}")
            with rapidgzip.RapidgzipFile(path, parallelization=0) as f:
                out = f.read()
            assert out == payload
            _phase("roundtrip-ok")
            """
        )
    elif scenario == "raw_gzip_bytesio_close":
        body += textwrap.dedent(
            """\
            import gzip, io
            import rapidgzip
            payload = b"rapidgzip-bytesio\\n" * 1500
            data = gzip.compress(payload)
            _phase(f"open_bytesio compressed={len(data)}")
            with rapidgzip.RapidgzipFile(io.BytesIO(data), parallelization=0) as f:
                out = f.read()
            assert out == payload
            _phase("roundtrip-ok")
            """
        )
    elif scenario == "raw_bzip2_bytesio_close":
        body += textwrap.dedent(
            """\
            import bz2, io
            import rapidgzip
            payload = b"indexed-bzip2-stress\\n" * 1500
            data = bz2.compress(payload)
            _phase(f"open_indexed_bzip2 compressed={len(data)}")
            with rapidgzip.IndexedBzip2File(io.BytesIO(data), parallelization=0) as f:
                out = f.read()
            assert out == payload
            _phase("roundtrip-ok")
            """
        )
    elif scenario == "archivey_gzip_bytesio":
        body += textwrap.dedent(
            """\
            import gzip, io
            from archivey.config import AcceleratorMode
            from archivey.internal.config import StreamConfig
            from archivey.internal.streams.codecs import Codec, open_codec_stream
            payload = b"archivey-gzip-accel\\n" * 1200
            data = gzip.compress(payload)
            cfg = StreamConfig(use_rapidgzip=AcceleratorMode.ON, seekable=True)
            _phase("open_codec_stream gzip ON")
            with open_codec_stream(Codec.GZIP, io.BytesIO(data), config=cfg) as stream:
                out = stream.read()
            assert out == payload
            _phase("roundtrip-ok")
            """
        )
    elif scenario == "archivey_bzip2_bytesio":
        body += textwrap.dedent(
            """\
            import bz2, io
            from archivey.config import AcceleratorMode
            from archivey.internal.config import StreamConfig
            from archivey.internal.streams.codecs import Codec, open_codec_stream
            payload = b"archivey-bzip2-accel\\n" * 1200
            data = bz2.compress(payload)
            cfg = StreamConfig(use_indexed_bzip2=AcceleratorMode.ON, seekable=True)
            _phase("open_codec_stream bzip2 ON")
            with open_codec_stream(Codec.BZIP2, io.BytesIO(data), config=cfg) as stream:
                out = stream.read()
            assert out == payload
            _phase("roundtrip-ok")
            """
        )
    elif scenario == "many_cycles_path":
        body += textwrap.dedent(
            f"""\
            import gzip
            import rapidgzip
            payload = b"cycle\\n" * 800
            path = work / "cycle.gz"
            path.write_bytes(gzip.compress(payload))
            cycles = 20 + ({seed} % 10)
            _phase(f"many_cycles={{cycles}}")
            for i in range(cycles):
                with rapidgzip.RapidgzipFile(path, parallelization=0) as f:
                    assert f.read() == payload
                if i % 5 == 0:
                    _phase(f"cycle={{i}}")
            _phase("cycles-ok")
            """
        )
    elif scenario == "truncated_gzip_close":
        body += textwrap.dedent(
            """\
            import gzip, io
            import rapidgzip
            payload = b"trunc-me\\n" * 2000
            data = gzip.compress(payload)[: len(gzip.compress(payload)) // 2]
            _phase(f"truncated_open compressed={len(data)}")
            f = rapidgzip.RapidgzipFile(io.BytesIO(data), parallelization=0)
            try:
                f.read()
            except Exception as exc:
                _phase(f"read_raised {type(exc).__name__}")
            finally:
                f.close()
            _phase("closed-after-truncation")
            """
        )
    elif scenario == "guard_cycle_gc":
        # Faithful copy of archivey's finalize-close guard; must exit cleanly.
        body += textwrap.dedent(
            """\
            import gc, gzip, io, weakref
            import rapidgzip

            payload = b"guard\\n" * 1000
            data = gzip.compress(payload)

            def _close(inner):
                try:
                    inner.close()
                except Exception:
                    pass

            class Guard:
                def __init__(self, inner):
                    self._inner = inner
                    self._fin = weakref.finalize(self, _close, inner)

            raw = rapidgzip.RapidgzipFile(io.BytesIO(data), parallelization=0)
            obj = Guard(raw)
            del raw
            try:
                obj._inner.read()
            except Exception:
                pass
            del obj
            gc.collect()
            _phase("guard-gc-ok")
            """
        )
    else:
        raise ValueError(f"unknown scenario: {scenario}")
    return body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "iterations",
        nargs="?",
        type=int,
        default=int(os.environ.get("ARCHIVEY_RAPIDGZIP_STRESS_ITERS", "20")),
        help="Child iterations per scenario (default: env or 20)",
    )
    parser.add_argument(
        "--scenarios",
        nargs="+",
        default=list(_DEFAULT_SCENARIOS),
        choices=list(_ALL_SCENARIOS),
    )
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--summary", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        import rapidgzip  # noqa: F401
    except ImportError:
        safe_print(
            "rapidgzip not installed; cannot stress. Install archivey[seekable].",
            file=sys.stderr,
        )
        return 2

    scenarios = list(dict.fromkeys(args.scenarios))
    return run_stress_matrix(
        title="rapidgzip native stress",
        scenarios=scenarios,
        iterations=args.iterations,
        timeout=args.timeout,
        work_env=_WORK_ENV,
        write_driver=_write_driver,
        summary_path=args.summary,
        footer=(
            "Known issue: rapidgzip aborts if worker threads outlive `close()`; "
            "also a leading suspect for intermittent Linux `[all]` suite heap "
            "corruption. See `docs/internal/known-issues.md`."
        ),
        tmp_prefix="archivey-rapidgzip-stress-",
    )


if __name__ == "__main__":
    raise SystemExit(main())
