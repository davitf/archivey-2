"""Benchmark harness: wall time, bytes decompressed, source seeks.

Run::

    uv run --extra all python -m benchmarks.harness
    uv run --extra all python -m benchmarks.harness --update-baselines
    uv run --extra all python -m benchmarks.harness --mode structural

Modes:

- ``structural`` (default for CI/PR): gate bytes-decompressed and seek invariants only.
- ``full``: also assert wall-time ratios vs stdlib peers (noisier; suited to nightly).
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import tarfile
import time
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from archivey import open_archive
from archivey.internal.base_reader import BaseArchiveReader
from archivey.internal.measurement import enable_measurement
from benchmarks.fixtures import FixtureSet, materialize_fixtures

ROOT = Path(__file__).resolve().parents[1]
BASELINES_DIR = Path(__file__).resolve().parent / "baselines"
STRUCTURAL_BASELINE = BASELINES_DIR / "structural.json"
WALL_BASELINE = BASELINES_DIR / "wall_time.json"

# Sequential solid read may decode a little padding / skip; keep a small slack factor.
SOLID_DECODE_FACTOR = 2.0
# Wall-time: start generous. Absolute budget is a sanity ceiling; regression gating uses
# the recorded baseline ± tolerance (VISION's 1.3× is the target on realistic corpora).
WALL_RATIO_BUDGET = 10.0
WALL_RATIO_TOLERANCE = 1.0
# VISION common-path target — reported in results, gated only in full mode on
# ``realistic`` scale (ci micro corpora are too small for a meaningful 1.3× claim).
WALL_RATIO_VISION = 1.3
WALL_RATIO_VISION_SAFETY = 2.0


@dataclass
class CaseResult:
    case: str
    format: str
    operation: str
    wall_s: float
    bytes_decompressed: int
    source_seek_count: int
    unpacked_bytes: int | None = None
    stdlib_wall_s: float | None = None
    wall_ratio: float | None = None
    notes: str = ""


def _as_base(reader: Any) -> BaseArchiveReader:
    assert isinstance(reader, BaseArchiveReader)
    return reader


def _op_open_list(path: Path) -> tuple[int, int]:
    with enable_measurement():
        with open_archive(path) as reader:
            base = _as_base(reader)
            _ = reader.info
            list(reader.members())
            return base.bytes_decompressed, base.source_seek_count


def _op_read_all(path: Path) -> tuple[int, int, int]:
    with enable_measurement():
        with open_archive(path) as reader:
            base = _as_base(reader)
            unpacked = 0
            for member, stream in reader.stream_members():
                if stream is not None:
                    data = stream.read()
                    unpacked += len(data)
            return base.bytes_decompressed, base.source_seek_count, unpacked


_extract_n = 0


def _op_extract(path: Path, dest_root: Path) -> tuple[int, int]:
    global _extract_n
    _extract_n += 1
    dest = dest_root / f"run-{_extract_n}"
    dest.mkdir(parents=True, exist_ok=True)
    with enable_measurement():
        with open_archive(path) as reader:
            base = _as_base(reader)
            reader.extract_all(dest)
            return base.bytes_decompressed, base.source_seek_count


def _op_read_all_unmeasured(path: Path) -> None:
    """Read every member without measurement wrappers — fair wall-time peer."""
    with open_archive(path) as reader:
        for _member, stream in reader.stream_members():
            if stream is not None:
                stream.read()


def _op_random_read_all(path: Path) -> tuple[int, int]:
    with enable_measurement():
        with open_archive(path) as reader:
            base = _as_base(reader)
            names = [m.name for m in reader.members() if m.is_file]
            for name in reversed(names):
                reader.read(name)
            return base.bytes_decompressed, base.source_seek_count


def _timed(fn: Callable[[], Any]) -> tuple[float, Any]:
    t0 = time.perf_counter()
    result = fn()
    return time.perf_counter() - t0, result


def _interleaved_pair_times(
    archivey_fn: Callable[[], Any],
    stdlib_fn: Callable[[], None],
    *,
    rounds: int = 5,
) -> tuple[float, Any, float]:
    """Alternate archivey/stdlib timing; return (median_ay_s, ay_result, median_std_s).

    One shared warmup of each side first. Alternating order removes the
    "whoever ran last left the page cache hot" bias. Diagnostic log spam is
    silenced for the timed window — ZIP name-encoding advisories otherwise
    flood stderr and skew sub-20ms samples (early runs reported archivey
    *faster* than ``zipfile``, which is impossible as a steady state since we
    wrap it).
    """
    import logging

    archivey_fn()
    stdlib_fn()
    ay_samples: list[float] = []
    std_samples: list[float] = []
    last_ay: Any = None
    prev_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        for i in range(rounds):
            if i % 2 == 0:
                wall, last_ay = _timed(archivey_fn)
                ay_samples.append(wall)
                wall, _ = _timed(stdlib_fn)
                std_samples.append(wall)
            else:
                wall, _ = _timed(stdlib_fn)
                std_samples.append(wall)
                wall, last_ay = _timed(archivey_fn)
                ay_samples.append(wall)
    finally:
        logging.disable(prev_disable)
    ay_samples.sort()
    std_samples.sort()
    mid = len(ay_samples) // 2
    return ay_samples[mid], last_ay, std_samples[mid]


def _stdlib_zip_read_all(path: Path) -> None:
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            info = zf.getinfo(name)
            if info.is_dir():
                continue
            zf.read(name)


def _stdlib_tar_read_all(path: Path) -> None:
    with tarfile.open(path, "r:") as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            f = tf.extractfile(member)
            if f is not None:
                f.read()


def _stdlib_gzip_read_all(path: Path) -> None:
    with gzip.open(path, "rb") as gf:
        gf.read()


def run_cases(
    fixtures: FixtureSet,
    work: Path,
    *,
    warmup: bool = False,
) -> list[CaseResult]:
    results: list[CaseResult] = []

    def timed_with_optional_warmup(fn: Callable[[], Any]) -> tuple[float, Any]:
        if warmup:
            fn()  # discard — fill page cache / import side effects
        return _timed(fn)

    # Wall-ratio cases: interleaved medians when warmup is on (realistic scale).
    # Structural metrics come from a separate measured pass — wall timing must not
    # install seek/output counters (those change the ZIP open path and skew ~10ms
    # samples enough to invent a bogus "faster than zipfile" ratio).
    def pair_wall(
        std_fn: Callable[[], None], path: Path
    ) -> tuple[float, float]:
        if warmup:
            ay_wall, _ignored, std_wall = _interleaved_pair_times(
                lambda: _op_read_all_unmeasured(path),
                std_fn,
                rounds=7,
            )
            return ay_wall, std_wall
        wall, _ = timed_with_optional_warmup(lambda: _op_read_all_unmeasured(path))
        std_wall, _ = timed_with_optional_warmup(std_fn)
        return wall, std_wall

    # --- ZIP ---
    wall, (bdec, seeks) = timed_with_optional_warmup(
        lambda: _op_open_list(fixtures.zip_path)
    )
    results.append(CaseResult("zip_open_list", "zip", "open_list", wall, bdec, seeks))
    # Structural bytes from a measured pass; wall ratio from an unmeasured peer race.
    _m_wall, (bdec, seeks, unpacked) = timed_with_optional_warmup(
        lambda: _op_read_all(fixtures.zip_path)
    )
    wall, std_wall = pair_wall(
        lambda: _stdlib_zip_read_all(fixtures.zip_path),
        fixtures.zip_path,
    )
    results.append(
        CaseResult(
            "zip_read_all",
            "zip",
            "read_all",
            wall,
            bdec,
            seeks,
            unpacked_bytes=unpacked,
            stdlib_wall_s=std_wall,
            wall_ratio=(wall / std_wall) if std_wall > 0 else None,
        )
    )
    wall, (bdec, seeks) = timed_with_optional_warmup(
        lambda: _op_extract(fixtures.zip_path, work / "extract-zip")
    )
    results.append(CaseResult("zip_extract", "zip", "extract", wall, bdec, seeks))

    # --- TAR (plain / uncompressed — harness peer is tarfile r:) ---
    wall, (bdec, seeks) = timed_with_optional_warmup(
        lambda: _op_open_list(fixtures.tar_path)
    )
    results.append(CaseResult("tar_open_list", "tar", "open_list", wall, bdec, seeks))
    _m_wall, (bdec, seeks, unpacked) = timed_with_optional_warmup(
        lambda: _op_read_all(fixtures.tar_path)
    )
    wall, std_wall = pair_wall(
        lambda: _stdlib_tar_read_all(fixtures.tar_path),
        fixtures.tar_path,
    )
    results.append(
        CaseResult(
            "tar_read_all",
            "tar",
            "read_all",
            wall,
            bdec,
            seeks,
            unpacked_bytes=unpacked,
            stdlib_wall_s=std_wall,
            wall_ratio=(wall / std_wall) if std_wall > 0 else None,
            notes="plain uncompressed TAR vs tarfile r: (not .tar.gz)",
        )
    )

    # --- gzip single-file ---
    _m_wall, (bdec, seeks, unpacked) = timed_with_optional_warmup(
        lambda: _op_read_all(fixtures.gzip_path)
    )
    wall, std_wall = pair_wall(
        lambda: _stdlib_gzip_read_all(fixtures.gzip_path),
        fixtures.gzip_path,
    )
    results.append(
        CaseResult(
            "gzip_read_all",
            "gzip",
            "read_all",
            wall,
            bdec,
            seeks,
            unpacked_bytes=unpacked,
            stdlib_wall_s=std_wall,
            wall_ratio=(wall / std_wall) if std_wall > 0 else None,
        )
    )

    # --- Solid 7z ---
    if fixtures.solid_7z is not None:
        wall, (bdec, seeks, unpacked) = timed_with_optional_warmup(
            lambda: _op_read_all(fixtures.solid_7z)  # type: ignore[arg-type]
        )
        results.append(
            CaseResult(
                "sevenzip_solid_sequential",
                "7z",
                "read_all_sequential",
                wall,
                bdec,
                seeks,
                unpacked_bytes=fixtures.unpacked_solid_7z,
                notes="solid invariant: bytes_decompressed <= unpacked * factor",
            )
        )
        wall, (bdec, seeks) = timed_with_optional_warmup(
            lambda: _op_random_read_all(fixtures.solid_7z)  # type: ignore[arg-type]
        )
        results.append(
            CaseResult(
                "sevenzip_solid_random",
                "7z",
                "read_all_random",
                wall,
                bdec,
                seeks,
                unpacked_bytes=fixtures.unpacked_solid_7z,
                notes="re-decode recorded; not gated",
            )
        )

    # --- Solid RAR ---
    if fixtures.solid_rar is not None:
        wall, (bdec, seeks, unpacked) = timed_with_optional_warmup(
            lambda: _op_read_all(fixtures.solid_rar)  # type: ignore[arg-type]
        )
        results.append(
            CaseResult(
                "rar_solid_sequential",
                "rar",
                "read_all_sequential",
                wall,
                bdec,
                seeks,
                unpacked_bytes=fixtures.unpacked_solid_rar,
                notes="solid invariant: bytes_decompressed <= unpacked * factor",
            )
        )
        wall, (bdec, seeks) = timed_with_optional_warmup(
            lambda: _op_random_read_all(fixtures.solid_rar)  # type: ignore[arg-type]
        )
        results.append(
            CaseResult(
                "rar_solid_random",
                "rar",
                "read_all_random",
                wall,
                bdec,
                seeks,
                unpacked_bytes=fixtures.unpacked_solid_rar,
                notes="re-decode recorded; not gated",
            )
        )

    return results


def _structural_checks(
    results: list[CaseResult],
    baseline: dict[str, Any] | None = None,
    *,
    check_seek_baselines: bool = True,
) -> list[str]:
    failures: list[str] = []
    cases = (baseline or {}).get("cases", {}) if baseline else {}
    for r in results:
        if r.case.endswith("_sequential") and r.unpacked_bytes:
            limit = int(r.unpacked_bytes * SOLID_DECODE_FACTOR)
            if r.bytes_decompressed > limit:
                failures.append(
                    f"{r.case}: bytes_decompressed={r.bytes_decompressed} "
                    f"> unpacked×{SOLID_DECODE_FACTOR}={limit} (solid re-decode?)"
                )
        if r.operation == "read_all" and r.unpacked_bytes is not None:
            # Common-path formats: decompressed bytes should match what was read.
            if (
                r.format in ("zip", "tar", "gzip")
                and r.bytes_decompressed < r.unpacked_bytes
            ):
                failures.append(
                    f"{r.case}: bytes_decompressed={r.bytes_decompressed} "
                    f"< unpacked={r.unpacked_bytes}"
                )
        # Seek counts: ≤ recorded baseline × 2 (host/path variance); missing baseline = skip.
        # Only meaningful against the same fixture scale as the committed baseline (ci).
        if check_seek_baselines:
            ref = cases.get(r.case)
            if ref is not None and "source_seek_count" in ref:
                limit = int(ref["source_seek_count"]) * 2 + 8
                if r.source_seek_count > limit:
                    failures.append(
                        f"{r.case}: source_seek_count={r.source_seek_count} > bound {limit}"
                    )
    return failures


def _wall_checks(
    results: list[CaseResult],
    baseline: dict[str, Any] | None,
    *,
    enforce_vision: bool = False,
) -> list[str]:
    failures: list[str] = []
    cases = (baseline or {}).get("cases", {}) if baseline else {}
    for r in results:
        if r.wall_ratio is None:
            continue
        if r.wall_ratio > WALL_RATIO_BUDGET:
            failures.append(
                f"{r.case}: wall_ratio={r.wall_ratio:.2f} > sanity budget {WALL_RATIO_BUDGET}"
            )
        if enforce_vision and r.wall_ratio > WALL_RATIO_VISION_SAFETY:
            failures.append(
                f"{r.case}: wall_ratio={r.wall_ratio:.2f} > VISION safety "
                f"{WALL_RATIO_VISION_SAFETY}× (target {WALL_RATIO_VISION}×)"
            )
        if r.case in cases:
            ref = float(cases[r.case]["wall_ratio"])
            if r.wall_ratio > ref + WALL_RATIO_TOLERANCE:
                failures.append(
                    f"{r.case}: wall_ratio={r.wall_ratio:.2f} > baseline {ref:.2f} "
                    f"+ tol {WALL_RATIO_TOLERANCE}"
                )
    return failures


def write_baselines(results: list[CaseResult]) -> None:
    BASELINES_DIR.mkdir(parents=True, exist_ok=True)
    structural: dict[str, Any] = {
        "solid_decode_factor": SOLID_DECODE_FACTOR,
        "cases": {},
    }
    wall: dict[str, Any] = {"wall_ratio_budget": WALL_RATIO_BUDGET, "cases": {}}
    for r in results:
        structural["cases"][r.case] = {
            "bytes_decompressed": r.bytes_decompressed,
            "source_seek_count": r.source_seek_count,
            "unpacked_bytes": r.unpacked_bytes,
            "notes": r.notes,
        }
        if r.wall_ratio is not None:
            wall["cases"][r.case] = {
                "wall_ratio": r.wall_ratio,
                "wall_s": r.wall_s,
                "stdlib_wall_s": r.stdlib_wall_s,
            }
    STRUCTURAL_BASELINE.write_text(json.dumps(structural, indent=2) + "\n")
    WALL_BASELINE.write_text(json.dumps(wall, indent=2) + "\n")


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("structural", "full"),
        default="structural",
        help="structural=bytes/seeks only (PR); full=include wall-time ratios",
    )
    parser.add_argument(
        "--scale",
        choices=("ci", "realistic"),
        default="ci",
        help="ci=small PR fixtures; realistic=multi-MiB corpora for wall-time",
    )
    parser.add_argument(
        "--warmup",
        action="store_true",
        help="Discard one pass per timed op before measuring (recommended with --scale realistic)",
    )
    parser.add_argument(
        "--update-baselines",
        action="store_true",
        help="Rewrite benchmarks/baselines/*.json from this run (ci scale only)",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Write full results JSON to this path",
    )
    parser.add_argument(
        "--fixture-dir",
        type=Path,
        default=None,
        help="Directory for on-demand fixtures (default: temp / ARCHIVEY_BENCH_CACHE)",
    )
    args = parser.parse_args(argv)

    if args.update_baselines and args.scale != "ci":
        print(
            "--update-baselines only writes the committed ci baselines; "
            f"refusing scale={args.scale!r}",
            file=sys.stderr,
        )
        return 2

    warmup = args.warmup or args.scale == "realistic"
    fixtures = materialize_fixtures(args.fixture_dir, scale=args.scale)
    work = fixtures.root / "work"
    if work.exists():
        import shutil

        shutil.rmtree(work)
    work.mkdir(parents=True)

    results = run_cases(fixtures, work, warmup=warmup)
    payload = {
        "mode": args.mode,
        "scale": fixtures.scale.name,
        "scale_detail": {
            "common_members": fixtures.scale.common_members,
            "common_member_size": fixtures.scale.common_member_size,
            "gzip_size": fixtures.scale.gzip_size,
            "solid_members": fixtures.scale.solid_members,
            "solid_member_size": fixtures.scale.solid_member_size,
        },
        "fixture_root": str(fixtures.root),
        "warmup": warmup,
        "results": [asdict(r) for r in results],
    }
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, indent=2) + "\n")

    print(json.dumps(payload, indent=2))

    if args.update_baselines:
        write_baselines(results)
        print(f"Updated baselines under {BASELINES_DIR}", file=sys.stderr)

    # Seek baselines are committed for the ci scale only.
    failures = _structural_checks(
        results,
        load_json(STRUCTURAL_BASELINE),
        check_seek_baselines=(args.scale == "ci"),
    )
    if args.mode == "full":
        wall_base = load_json(WALL_BASELINE) if args.scale == "ci" else None
        # Sanity ceiling (+ ci baseline drift) hard-fail. VISION ≤1.3× / ~2× on
        # realistic corpora is printed as informational until variance is settled.
        for f in _wall_checks(results, wall_base, enforce_vision=False):
            failures.append(f)
        if args.scale == "realistic":
            for f in _wall_checks(results, None, enforce_vision=True):
                if "VISION safety" in f:
                    print(f"VISION BUDGET (informational): {f}", file=sys.stderr)

    if failures:
        print("BENCHMARK GATE FAILURES:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
