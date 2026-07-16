#!/usr/bin/env python3
"""Stress-test native 7z PPMd roundtrips (Windows heap-corruption investigation).

Background: on ``windows-latest`` the regular
``test_py7zr_codec_fixtures_roundtrip[ppmd]`` has intermittently aborted with
``STATUS_HEAP_CORRUPTION`` (``0xC0000374``) inside ``pyppmd`` while skipping forward
through a *valid* solid PPMd folder to open the second member. Isolation pinned the
codec; root cause is not fixed. See ``docs/internal/known-issues.md``.

This script re-runs the same happy-path roundtrip many times in fresh subprocesses so
CI can collect a crash rate without blocking the main Windows matrix (which skips the
PPMd param). It is the dedicated investigation vehicle:

    uv run --extra all python scripts/windows_ppmd_stress.py
    uv run --extra all python scripts/windows_ppmd_stress.py 80   # more iters
    ARCHIVEY_PPMD_STRESS_ITERS=50 uv run --extra all python scripts/windows_ppmd_stress.py

Exit code is non-zero if any child crashed or failed — useful as a red (non-required)
check on the ``Windows PPMd stress`` workflow.
"""

from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

# NTSTATUS values Windows has surfaced for native aborts in this suite.
_WINDOWS_NTSTATUS: dict[int, str] = {
    0xC0000005: "STATUS_ACCESS_VIOLATION",
    0xC0000374: "STATUS_HEAP_CORRUPTION",
    0xC0000409: "STATUS_STACK_BUFFER_OVERRUN",
    0xC00000FD: "STATUS_STACK_OVERFLOW",
    0xC0000094: "STATUS_INTEGER_DIVIDE_BY_ZERO",
    0x80000003: "STATUS_BREAKPOINT",
}


def _format_rc(returncode: int) -> str:
    unsigned = returncode & 0xFFFFFFFF
    if returncode < 0 or returncode > 255:
        name = _WINDOWS_NTSTATUS.get(unsigned)
        if name is not None:
            return f"0x{unsigned:08X} ({name}); signed={returncode}"
        if -64 < returncode < 0:
            return f"{returncode} (likely signal {-returncode})"
        return f"0x{unsigned:08X} (unknown); signed={returncode}"
    return str(returncode)


def _write_driver(path: Path, work: Path) -> None:
    """Child: build a py7zr PPMd solid archive and read both members via archivey."""
    path.write_text(
        textwrap.dedent(
            f"""\
            from __future__ import annotations
            import faulthandler
            import os
            import sys
            from pathlib import Path

            faulthandler.enable(all_threads=True, file=sys.stderr)
            work = Path({str(work)!r})
            phase_path = work / "phase.txt"
            archive_path = work / "ppmd.7z"

            def _phase(msg: str) -> None:
                line = msg + "\\n"
                with phase_path.open("a", encoding="utf-8") as fh:
                    fh.write(line)
                    fh.flush()
                    os.fsync(fh.fileno())
                print(f"[phase] {{msg}}", flush=True)

            _phase("start")
            from archivey import open_archive
            from tests.test_sevenzip_reader import (
                _FILES,
                _filters,
                _write_py7zr_archive,
            )

            _phase("imports-ok")
            _phase("building-archive")
            _write_py7zr_archive(archive_path, _FILES, filters=_filters("PPMD"))
            size = archive_path.stat().st_size
            head = archive_path.read_bytes()[:32].hex()
            _phase(f"archive-built size={{size}} head32={{head}}")
            _phase("open_archive")
            with open_archive(archive_path) as archive:
                members = {{
                    m.name: m for m in archive.members() if m.is_file
                }}
                _phase(f"listed count={{len(members)}} names={{sorted(members)!r}}")
                assert set(members) == set(_FILES)
                # Sorted order matches the regular CI test: alpha.txt then nested/beta.bin.
                # The known crash is during skip_forward when opening the second member
                # after a successful first-member read (valid solid PPMd stream).
                for name in sorted(_FILES):
                    _phase(f"read_member:{{name}}:start")
                    data = archive.read(members[name])
                    _phase(f"read_member:{{name}}:done len={{len(data)}}")
                    assert data == _FILES[name]
            _phase("roundtrip-ok")
            """
        ),
        encoding="utf-8",
    )


def _one_iteration(iter_dir: Path, timeout: float) -> tuple[int, str, str, str]:
    """Run one isolated roundtrip. Returns (rc, phase_text, stdout, stderr)."""
    iter_dir.mkdir(parents=True, exist_ok=True)
    phase_path = iter_dir / "phase.txt"
    driver = iter_dir / "_driver.py"
    _write_driver(driver, iter_dir)
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(_REPO_ROOT / "src"),
            str(_REPO_ROOT / "tests"),
            str(_REPO_ROOT),
            env.get("PYTHONPATH", ""),
        ]
    )
    env.setdefault("PYTHONFAULTHANDLER", "1")
    proc = subprocess.run(
        [sys.executable, "-u", str(driver)],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
        check=False,
        cwd=str(_REPO_ROOT),
    )
    phase = (
        phase_path.read_text(encoding="utf-8") if phase_path.exists() else "<missing>"
    )
    return proc.returncode, phase, proc.stdout, proc.stderr


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "iterations",
        nargs="?",
        type=int,
        default=int(os.environ.get("ARCHIVEY_PPMD_STRESS_ITERS", "40")),
        help="Number of isolated roundtrips (default: env ARCHIVEY_PPMD_STRESS_ITERS or 40)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Per-iteration subprocess timeout in seconds (default: 120)",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=None,
        help="Optional path to write a Markdown summary (for GITHUB_STEP_SUMMARY)",
    )
    args = parser.parse_args(argv)

    try:
        import pyppmd  # noqa: F401
    except ImportError:
        print(
            "pyppmd not installed; cannot stress PPMd. Install archivey[7z].",
            file=sys.stderr,
        )
        return 2
    try:
        import py7zr  # noqa: F401
    except ImportError:
        print("py7zr not installed; cannot build PPMd fixtures.", file=sys.stderr)
        return 2

    print(
        f"Windows PPMd stress: iterations={args.iterations} "
        f"platform={platform.platform()!r} python={sys.version.split()[0]} "
        f"executable={sys.executable!r}",
        flush=True,
    )

    crashes: list[tuple[int, int, str]] = []  # (iter, rc, last_phase)
    failures: list[tuple[int, int, str]] = []
    passes = 0

    with tempfile.TemporaryDirectory(prefix="archivey-ppmd-stress-") as tmp:
        root = Path(tmp)
        for i in range(1, args.iterations + 1):
            iter_dir = root / f"iter-{i:04d}"
            try:
                rc, phase, _stdout, stderr = _one_iteration(iter_dir, args.timeout)
            except subprocess.TimeoutExpired:
                failures.append((i, -1, "timeout"))
                print(f"  [{i}/{args.iterations}] TIMEOUT", flush=True)
                continue
            last_phase = phase.strip().splitlines()[-1] if phase.strip() else "<empty>"
            if rc == 0:
                passes += 1
                print(f"  [{i}/{args.iterations}] ok", flush=True)
                continue
            unsigned = rc & 0xFFFFFFFF
            is_crash = unsigned in _WINDOWS_NTSTATUS or rc < 0 or rc > 255
            bucket = crashes if is_crash else failures
            bucket.append((i, rc, last_phase))
            kind = "CRASH" if is_crash else "FAIL"
            print(
                f"  [{i}/{args.iterations}] {kind} rc={_format_rc(rc)} "
                f"last_phase={last_phase!r}",
                flush=True,
            )
            if stderr.strip():
                # Keep stderr short in the loop log; full dumps go to the summary.
                tail = "\n".join(stderr.strip().splitlines()[-8:])
                print(f"    stderr tail:\n{textwrap.indent(tail, '    ')}", flush=True)

    total = args.iterations
    lines = [
        "# Windows PPMd stress results",
        "",
        f"- platform: `{platform.platform()}`",
        f"- python: `{sys.version.split()[0]}`",
        f"- iterations: **{total}**",
        f"- passes: **{passes}**",
        f"- native crashes: **{len(crashes)}**",
        f"- other failures: **{len(failures)}**",
        "",
    ]
    if crashes:
        lines.append("## Crashes")
        lines.append("")
        for i, rc, last_phase in crashes:
            lines.append(f"- iter {i}: `{_format_rc(rc)}` at phase `{last_phase}`")
        lines.append("")
    if failures:
        lines.append("## Other failures")
        lines.append("")
        for i, rc, last_phase in failures:
            lines.append(f"- iter {i}: `{_format_rc(rc)}` at phase `{last_phase}`")
        lines.append("")
    lines.append(
        "Known issue: valid solid PPMd 7z (py7zr-built) → intermittent "
        "`STATUS_HEAP_CORRUPTION` in `pyppmd` on Windows during second-member "
        "`skip_forward`. See `docs/internal/known-issues.md`."
    )
    summary = "\n".join(lines) + "\n"
    print(summary, flush=True)
    if args.summary is not None:
        args.summary.write_text(summary, encoding="utf-8")
    gh_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if gh_summary:
        with open(gh_summary, "a", encoding="utf-8") as fh:
            fh.write(summary)

    if crashes or failures:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
