"""Shared helpers for per-native-library stress scripts (PPMd / rapidgzip / inflate64 / …).

Each library keeps its own ``scripts/<lib>_native_stress.py`` driver so scenarios stay
focused; this module owns the subprocess loop, Windows NTSTATUS formatting, phase
file plumbing, and Markdown summary writing. See ``docs/internal/known-issues.md``.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import tempfile
import textwrap
from collections.abc import Callable, Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

WINDOWS_NTSTATUS: dict[int, str] = {
    0xC0000005: "STATUS_ACCESS_VIOLATION",
    0xC0000374: "STATUS_HEAP_CORRUPTION",
    0xC0000409: "STATUS_STACK_BUFFER_OVERRUN",
    0xC00000FD: "STATUS_STACK_OVERFLOW",
    0xC0000094: "STATUS_INTEGER_DIVIDE_BY_ZERO",
    0x80000003: "STATUS_BREAKPOINT",
}


def safe_print(msg: str, *, file=None) -> None:
    stream = file or sys.stdout
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        stream.write(msg + "\n")
    except UnicodeEncodeError:
        stream.write(msg.encode(encoding, errors="replace").decode(encoding) + "\n")
    stream.flush()


def format_rc(returncode: int) -> str:
    unsigned = returncode & 0xFFFFFFFF
    if returncode < 0 or returncode > 255:
        name = WINDOWS_NTSTATUS.get(unsigned)
        if name is not None:
            return f"0x{unsigned:08X} ({name}); signed={returncode}"
        if -64 < returncode < 0:
            return f"{returncode} (likely signal {-returncode})"
        return f"0x{unsigned:08X} (unknown); signed={returncode}"
    return str(returncode)


def phase_helpers(*, work_env: str) -> str:
    """Child-driver preamble: faulthandler + append-only phase log under ``work_env``."""
    return textwrap.dedent(
        f"""\
        from __future__ import annotations
        import faulthandler
        import os
        import sys
        from pathlib import Path

        faulthandler.enable(all_threads=True, file=sys.stderr)
        work = Path(os.environ[{work_env!r}])
        phase_path = work / "phase.txt"

        def _phase(msg: str) -> None:
            line = msg + "\\n"
            with phase_path.open("a", encoding="utf-8") as fh:
                fh.write(line)
                fh.flush()
                os.fsync(fh.fileno())
            print(f"[phase] {{msg}}", flush=True)

        _phase("start")
        """
    )


def run_child(
    *,
    iter_dir: Path,
    driver_source: str,
    work_env: str,
    timeout: float,
) -> tuple[int, str, str, str]:
    """Write ``_driver.py``, run it, return ``(rc, phase_text, stdout, stderr)``."""
    iter_dir.mkdir(parents=True, exist_ok=True)
    phase_path = iter_dir / "phase.txt"
    driver = iter_dir / "_driver.py"
    driver.write_text(driver_source, encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(REPO_ROOT / "src"),
            str(REPO_ROOT / "tests"),
            str(REPO_ROOT),
            env.get("PYTHONPATH", ""),
        ]
    )
    env[work_env] = str(iter_dir)
    env.setdefault("PYTHONFAULTHANDLER", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    proc = subprocess.run(
        [sys.executable, "-u", str(driver)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=timeout,
        check=False,
        cwd=str(REPO_ROOT),
    )
    phase = (
        phase_path.read_text(encoding="utf-8") if phase_path.exists() else "<missing>"
    )
    return proc.returncode, phase, proc.stdout, proc.stderr


def run_stress_matrix(
    *,
    title: str,
    scenarios: Sequence[str],
    iterations: int,
    timeout: float,
    work_env: str,
    write_driver: Callable[[str, int], str],
    summary_path: Path | None,
    footer: str,
    tmp_prefix: str,
) -> int:
    """Run ``iterations`` fresh children per scenario; return 0 on all-ok, else 1."""
    safe_print(
        f"{title}: scenarios={list(scenarios)!r} "
        f"iters_per_scenario={iterations} "
        f"platform={platform.platform()!r} python={sys.version.split()[0]} "
        f"executable={sys.executable!r}"
    )

    crashes: list[tuple[str, int, int, str]] = []
    failures: list[tuple[str, int, int, str]] = []
    passes_by_scenario: dict[str, int] = dict.fromkeys(scenarios, 0)
    total_runs = 0

    with tempfile.TemporaryDirectory(prefix=tmp_prefix) as tmp:
        root = Path(tmp)
        for scenario in scenarios:
            safe_print(f"== scenario {scenario} ==")
            for i in range(1, iterations + 1):
                total_runs += 1
                iter_dir = root / scenario / f"iter-{i:04d}"
                try:
                    rc, phase, _stdout, stderr = run_child(
                        iter_dir=iter_dir,
                        driver_source=write_driver(scenario, i - 1),
                        work_env=work_env,
                        timeout=timeout,
                    )
                except subprocess.TimeoutExpired:
                    failures.append((scenario, i, -1, "timeout"))
                    safe_print(f"  [{scenario} {i}/{iterations}] TIMEOUT")
                    continue
                last_phase = (
                    phase.strip().splitlines()[-1] if phase.strip() else "<empty>"
                )
                if rc == 0:
                    passes_by_scenario[scenario] += 1
                    safe_print(f"  [{scenario} {i}/{iterations}] ok")
                    continue
                unsigned = rc & 0xFFFFFFFF
                is_crash = unsigned in WINDOWS_NTSTATUS or rc < 0 or rc > 255
                bucket = crashes if is_crash else failures
                bucket.append((scenario, i, rc, last_phase))
                kind = "CRASH" if is_crash else "FAIL"
                safe_print(
                    f"  [{scenario} {i}/{iterations}] {kind} "
                    f"rc={format_rc(rc)} last_phase={last_phase!r}"
                )
                if stderr.strip():
                    tail = "\n".join(stderr.strip().splitlines()[-8:])
                    safe_print(f"    stderr tail:\n{textwrap.indent(tail, '    ')}")

    lines = [
        f"# {title} results",
        "",
        f"- platform: `{platform.platform()}`",
        f"- python: `{sys.version.split()[0]}`",
        f"- scenarios: `{', '.join(scenarios)}`",
        f"- total child runs: **{total_runs}**",
        f"- native crashes: **{len(crashes)}**",
        f"- other failures: **{len(failures)}**",
        "",
        "## Passes by scenario",
        "",
    ]
    for scenario in scenarios:
        lines.append(
            f"- `{scenario}`: **{passes_by_scenario[scenario]}** / {iterations}"
        )
    lines.append("")
    if crashes:
        lines.append("## Crashes")
        lines.append("")
        for scenario, i, rc, last_phase in crashes:
            lines.append(
                f"- `{scenario}` iter {i}: `{format_rc(rc)}` at phase `{last_phase}`"
            )
        lines.append("")
    if failures:
        lines.append("## Other failures")
        lines.append("")
        for scenario, i, rc, last_phase in failures:
            lines.append(
                f"- `{scenario}` iter {i}: `{format_rc(rc)}` at phase `{last_phase}`"
            )
        lines.append("")
    lines.append(footer)
    summary = "\n".join(lines) + "\n"

    if summary_path is not None:
        summary_path.write_text(summary, encoding="utf-8")
    gh_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if gh_summary:
        with open(gh_summary, "a", encoding="utf-8") as fh:
            fh.write(summary)
    safe_print(summary)

    if crashes or failures:
        return 1
    return 0
