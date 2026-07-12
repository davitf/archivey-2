"""CLI entry: ``python -m tests.atheris_fuzz``.

libFuzzer/Atheris allows ``Setup()`` only once per process, so multi-target runs
spawn one child per target. Each child imports ``archivey`` under instrumentation.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    from tests.atheris_fuzz import TARGET_NAMES

    parser = argparse.ArgumentParser(
        prog="python -m tests.atheris_fuzz",
        description="Coverage-guided Atheris fuzz harness for archivey parsers/entry points.",
    )
    parser.add_argument(
        "--target",
        choices=TARGET_NAMES,
        help="Run a single named target (default: all with positive budget).",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="1s budget per target — verifies seeds + instrumentation only.",
    )
    parser.add_argument(
        "--repro",
        type=Path,
        help="Replay a single crashing input through the selected --target.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print targets and default budgets, then exit.",
    )
    parser.add_argument(
        "--worker",
        action="store_true",
        help=argparse.SUPPRESS,  # internal: single-target worker process
    )
    return parser.parse_args(argv)


def _load_targets_instrumented() -> dict:
    import atheris

    with atheris.instrument_imports(["archivey"]):
        from tests.atheris_fuzz.targets import iter_target_specs, rar_available

    return {
        "specs": {s["name"]: s for s in iter_target_specs()},
        "rar_available": rar_available,
    }


def _run_worker(args: argparse.Namespace) -> int:
    from tests.atheris_fuzz.runner import run_repro, run_target

    assert args.target is not None
    loaded = _load_targets_instrumented()
    spec = loaded["specs"][args.target]
    skip_unless = spec.get("skip_unless")
    if skip_unless is not None and not skip_unless():
        print(f"[atheris] skipping {args.target}: skip_unless not met", flush=True)
        return 0

    if args.repro is not None:
        return run_repro(
            name=args.target,
            test_one_input=spec["fn"],
            path=args.repro,
            fixup=spec.get("fixup"),
            per_input_timeout=spec.get("per_input_timeout"),
        )

    print(f"[atheris] running {args.target}…", flush=True)
    return run_target(
        name=args.target,
        test_one_input=spec["fn"],
        seeds=list(spec["seeds"]()),
        fixup=spec.get("fixup"),
        per_input_timeout=spec.get("per_input_timeout"),
        smoke=args.smoke,
    )


def _spawn_target(name: str, *, smoke: bool, repro: Path | None) -> int:
    cmd = [sys.executable, "-m", "tests.atheris_fuzz", "--worker", "--target", name]
    if smoke:
        cmd.append("--smoke")
    if repro is not None:
        cmd.extend(["--repro", str(repro)])
    print(f"[atheris] spawn {' '.join(cmd)}", flush=True)
    completed = subprocess.run(cmd, check=False, env=os.environ.copy())
    return int(completed.returncode)


def main(argv: list[str] | None = None) -> int:
    from tests.atheris_fuzz import DEFAULT_BUDGETS, TARGET_NAMES

    args = _parse_args(argv)

    if args.list:
        from tests.atheris_fuzz.targets import rar_available, rar_open_available

        for name in TARGET_NAMES:
            skip = ""
            if name == "rar_header" and not rar_available():
                skip = " (skipped: RAR backend not registered)"
            elif name == "rar" and not rar_open_available():
                if not rar_available():
                    skip = " (skipped: RAR backend not registered)"
                else:
                    skip = " (skipped: RARLAB unrar not on PATH)"
            print(f"{name:20s} default={DEFAULT_BUDGETS[name]}s{skip}")
        return 0

    if args.worker:
        if not args.target:
            print("--worker requires --target", file=sys.stderr)
            return 2
        return _run_worker(args)

    if args.repro is not None:
        if not args.target:
            print("--repro requires --target", file=sys.stderr)
            return 2
        return _spawn_target(args.target, smoke=False, repro=args.repro)

    selected = [args.target] if args.target else list(TARGET_NAMES)
    exit_code = 0
    for name in selected:
        # Always spawn: libFuzzer Setup() is once-per-process.
        code = _spawn_target(name, smoke=args.smoke, repro=None)
        if code != 0:
            exit_code = code
            print(f"[atheris] {name} FAILED (exit={code})", file=sys.stderr, flush=True)
        else:
            print(f"[atheris] {name} ok", flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
