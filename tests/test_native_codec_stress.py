"""Subprocess stress for rapidgzip / inflate64 natives (non-required CI).

Excluded from the default suite via the ``native_codec_stress`` marker (selected
only by the non-blocking native-codec stress workflow). See
``docs/internal/known-issues.md``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import requires

pytestmark = [
    pytest.mark.native_codec_stress,
]

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _iters(env_name: str) -> int:
    return int(os.environ.get(env_name, "10"))


def _run(
    script: str, env_iters: str, *scenarios: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(_REPO_ROOT / "scripts" / script),
            str(_iters(env_iters)),
            "--scenarios",
            *scenarios,
        ],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


@requires("rapidgzip")
def test_rapidgzip_minimal_surface_subprocess_stress() -> None:
    """Fresh-process rapidgzip path + BytesIO close cycles (gzip + IndexedBzip2)."""
    proc = _run(
        "rapidgzip_native_stress.py",
        "ARCHIVEY_RAPIDGZIP_STRESS_ITERS",
        "raw_gzip_path_close",
        "raw_gzip_bytesio_close",
        "raw_bzip2_bytesio_close",
        "archivey_gzip_bytesio",
    )
    assert proc.returncode == 0, (
        "rapidgzip minimal-surface stress saw crashes/failures "
        f"(rc={proc.returncode}).\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}\n"
    )


@requires("rapidgzip")
def test_rapidgzip_truncated_and_guard_subprocess_stress() -> None:
    """Truncated gzip still closes cleanly; finalize-guard GC must not abort."""
    proc = _run(
        "rapidgzip_native_stress.py",
        "ARCHIVEY_RAPIDGZIP_STRESS_ITERS",
        "truncated_gzip_close",
        "guard_cycle_gc",
    )
    assert proc.returncode == 0, (
        "rapidgzip truncation/guard stress saw crashes/failures "
        f"(rc={proc.returncode}).\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}\n"
    )


@requires("inflate64")
def test_inflate64_raw_and_bounded_subprocess_stress() -> None:
    """Fresh-process inflate64 Inflater cycles + archivey budgeted read(1)."""
    proc = _run(
        "inflate64_native_stress.py",
        "ARCHIVEY_INFLATE64_STRESS_ITERS",
        "raw_inflate_roundtrip",
        "raw_inflate_many_cycles",
        "archivey_bounded_read1",
        "truncated_flush",
    )
    assert proc.returncode == 0, (
        "inflate64 stress saw crashes/failures "
        f"(rc={proc.returncode}).\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}\n"
    )
