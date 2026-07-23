#!/usr/bin/env python3
"""Stress-test inflate64 / Deflate64 native decode surfaces.

Background
----------
``inflate64`` backs ZIP/7z Deflate64 members. Unlike zlib, it has **no** output-size
parameter: a small highly-compressible feed can expand hugely in one ``inflate()``.
archivey bounds peaks via ``Deflate64Decoder``'s budgeted feed under ``max_length``
(see ``src/archivey/internal/streams/decompress.py``).

No intermittent native-abort flake is pinned to inflate64 yet (unlike pyppmd /
rapidgzip); this harness is the soak vehicle so we notice if one appears, and so
the budgeted-read contract stays exercised under process isolation.

Default scenarios::

    uv run --extra all python scripts/inflate64_native_stress.py
    uv run --extra all python scripts/inflate64_native_stress.py 40 \\
        --scenarios raw_inflate_roundtrip archivey_bounded_read1

Requires the ``7z`` CLI for ZIP-fixture scenarios (those soft-skip inside the child
when ``7z`` is absent). Exit code is non-zero if any child crashed or failed.
"""

from __future__ import annotations

import argparse
import os
import shutil
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

_WORK_ENV = "ARCHIVEY_INFLATE64_STRESS_WORK"

_DEFAULT_SCENARIOS: tuple[str, ...] = (
    "raw_inflate_roundtrip",
    "raw_inflate_many_cycles",
    "archivey_bounded_read1",
    "archivey_readall",
    "truncated_flush",
)

_ALL_SCENARIOS: tuple[str, ...] = (
    *_DEFAULT_SCENARIOS,
    "zip_deflate64_member",  # needs 7z CLI
)

_ZIP_MEMBER_HELPER = textwrap.dedent(
    """\
    import shutil, struct, subprocess

    def _deflate64_member_bytes(payload: bytes, label: str) -> bytes | None:
        if shutil.which("7z") is None:
            _phase("skip-no-7z")
            return None
        src = work / f"{label}.txt"
        src.write_bytes(payload)
        archive = work / f"{label}.zip"
        r = subprocess.run(
            ["7z", "a", "-tzip", "-mm=Deflate64", str(archive), str(src)],
            capture_output=True,
            text=True,
            check=False,
        )
        if r.returncode != 0:
            _phase(f"7z-failed rc={r.returncode}")
            return None
        raw = archive.read_bytes()
        assert raw[:4] == b"PK\\x03\\x04"
        method = struct.unpack_from("<H", raw, 8)[0]
        name_len, extra_len = struct.unpack_from("<HH", raw, 26)
        comp_start = 30 + name_len + extra_len
        comp_size = struct.unpack_from("<I", raw, 18)[0]
        if method != 9:
            _phase(f"skip-not-deflate64 method={method}")
            return None
        return raw[comp_start : comp_start + comp_size]
    """
)


def _write_driver(scenario: str, seed: int) -> str:
    body = phase_helpers(work_env=_WORK_ENV)
    if scenario == "raw_inflate_roundtrip":
        body += textwrap.dedent(
            """\
            import inflate64
            payload = b"inflate64-stress\\n" * 2000
            if not hasattr(inflate64, "Deflater"):
                _phase("skip-no-Deflater")
                raise SystemExit(0)
            _phase("deflate_via_inflate64.Deflater")
            d = inflate64.Deflater()
            packed = d.deflate(payload) + d.flush()
            _phase(f"inflate packed={len(packed)}")
            inf = inflate64.Inflater()
            out = inf.inflate(packed)
            if not inf.eof:
                out += inf.inflate(b"")
            assert out == payload
            _phase("roundtrip-ok")
            """
        )
    elif scenario == "raw_inflate_many_cycles":
        body += textwrap.dedent(
            f"""\
            import inflate64
            payload = b"cycle64\\n" * 500
            if not hasattr(inflate64, "Deflater"):
                _phase("skip-no-Deflater")
                raise SystemExit(0)
            d = inflate64.Deflater()
            packed = d.deflate(payload) + d.flush()
            cycles = 25 + ({seed} % 10)
            _phase(f"cycles={{cycles}}")
            for i in range(cycles):
                inf = inflate64.Inflater()
                out = inf.inflate(packed)
                if not inf.eof:
                    out += inf.inflate(b"")
                assert out == payload
                if i % 5 == 0:
                    _phase(f"cycle={{i}}")
            _phase("cycles-ok")
            """
        )
    elif scenario == "archivey_bounded_read1":
        body += _ZIP_MEMBER_HELPER
        body += textwrap.dedent(
            """\
            import io
            from archivey.internal.streams.decompress import Deflate64DecompressorStream

            payload = b"A" * 200_000
            comp = _deflate64_member_bytes(payload, "bounded")
            if comp is None:
                raise SystemExit(0)
            _phase(f"bounded_read1 compressed={len(comp)}")
            with Deflate64DecompressorStream(io.BytesIO(comp)) as stream:
                first = stream.read(1)
                assert first == b"A"
                assert len(stream._buffer) < 128_000
                rest = stream.read()
            assert first + rest == payload
            _phase("bounded-ok")
            """
        )
    elif scenario == "archivey_readall":
        body += _ZIP_MEMBER_HELPER
        body += textwrap.dedent(
            """\
            import io
            from archivey.internal.streams.decompress import Deflate64DecompressorStream

            payload = b"readall-d64\\n" * 800
            comp = _deflate64_member_bytes(payload, "readall")
            if comp is None:
                raise SystemExit(0)
            _phase(f"readall compressed={len(comp)}")
            with Deflate64DecompressorStream(io.BytesIO(comp)) as stream:
                out = stream.read()
            assert out == payload
            _phase("readall-ok")
            """
        )
    elif scenario == "truncated_flush":
        body += _ZIP_MEMBER_HELPER
        body += textwrap.dedent(
            """\
            import io
            from archivey.exceptions import TruncatedError
            from archivey.internal.streams.decompress import Deflate64DecompressorStream

            payload = b"trunc-d64\\n" * 1000
            comp = _deflate64_member_bytes(payload, "trunc")
            if comp is None:
                raise SystemExit(0)
            truncated = comp[: max(1, len(comp) // 2)]
            _phase(f"truncated compressed={len(truncated)}/{len(comp)}")
            with Deflate64DecompressorStream(io.BytesIO(truncated)) as stream:
                try:
                    stream.read()
                    _phase("unexpected-success")
                    raise SystemExit(1)
                except TruncatedError:
                    _phase("truncated-ok")
            """
        )
    elif scenario == "zip_deflate64_member":
        body += textwrap.dedent(
            """\
            import shutil, subprocess
            from archivey import open_archive

            if shutil.which("7z") is None:
                _phase("skip-no-7z")
                raise SystemExit(0)
            payload = b"zip-member-d64\\n" * 600
            src = work / "member.txt"
            src.write_bytes(payload)
            archive = work / "member.zip"
            r = subprocess.run(
                ["7z", "a", "-tzip", "-mm=Deflate64", str(archive), str(src)],
                capture_output=True,
                text=True,
                check=False,
            )
            if r.returncode != 0:
                _phase(f"7z-failed rc={r.returncode}")
                raise SystemExit(0)
            _phase("open_archive")
            with open_archive(archive) as ar:
                members = [m for m in ar.members() if m.is_file]
                assert members
                data = ar.read(members[0])
            assert data == payload
            _phase("zip-member-ok")
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
        default=int(os.environ.get("ARCHIVEY_INFLATE64_STRESS_ITERS", "20")),
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
        import inflate64  # noqa: F401
    except ImportError:
        safe_print(
            "inflate64 not installed; cannot stress. Install archivey[7z].",
            file=sys.stderr,
        )
        return 2

    scenarios = list(dict.fromkeys(args.scenarios))
    needs_7z = any(
        s
        in {
            "archivey_bounded_read1",
            "archivey_readall",
            "truncated_flush",
            "zip_deflate64_member",
        }
        for s in scenarios
    )
    if needs_7z and shutil.which("7z") is None:
        safe_print(
            "warning: 7z CLI not on PATH; 7z-backed scenarios will soft-skip "
            "inside each child.",
            file=sys.stderr,
        )

    return run_stress_matrix(
        title="inflate64 native stress",
        scenarios=scenarios,
        iterations=args.iterations,
        timeout=args.timeout,
        work_env=_WORK_ENV,
        write_driver=_write_driver,
        summary_path=args.summary,
        footer=(
            "inflate64 has no output-size parameter; archivey bounds via budgeted "
            "feeds under max_length. No pinned intermittent abort yet — this soak "
            "is the early-warning vehicle. See `docs/internal/known-issues.md`."
        ),
        tmp_prefix="archivey-inflate64-stress-",
    )


if __name__ == "__main__":
    raise SystemExit(main())
