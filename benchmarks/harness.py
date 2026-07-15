"""Benchmark harness: wall time, bytes decompressed, source seeks.

Run::

    uv run --extra all python -m benchmarks.harness
    uv run --extra all python -m benchmarks.harness --update-baselines
    uv run --extra all python -m benchmarks.harness --mode structural
    uv run --extra all python -m benchmarks.harness --mode full --scale realistic

Modes:

- ``structural`` (default for CI/PR): **the automated gate** — seek-count baselines
  and solid decode-once bounds only. Common-path ``bytes_decompressed == unpacked``
  is tautological (counted at member output); the seek axis is what catches
  silent re-open churn.
- ``full``: also report wall-time ratios vs stdlib peers. Ratios are noisy on
  shared runners, so full mode is a **manual / nightly drift tool**, not the PR
  gate. Sanity ceiling only (no committed wall-time baseline).

Formats covered here: ZIP, TAR, gzip, tar.gz/tar.bz2 (+ accelerators), solid 7z
(and solid RAR when the ``rar`` writer is available to build fixtures). ISO and
directory backends are instrumented for measurement but deliberately out of
scope for this harness — see ``benchmarks/tar_iso_lock_baseline.py`` for ISO.
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

from archivey import MemberStreams, open_archive
from archivey.config import AcceleratorMode, ArchiveyConfig
from archivey.internal.base_reader import BaseArchiveReader
from archivey.internal.measurement import enable_measurement
from benchmarks.fixtures import FixtureSet, materialize_fixtures

ROOT = Path(__file__).resolve().parents[1]
BASELINES_DIR = Path(__file__).resolve().parent / "baselines"
STRUCTURAL_BASELINE = BASELINES_DIR / "structural.json"

# Sequential solid read may decode a little padding / skip; keep a small slack factor.
# (Unit tests use a tighter bound on controlled fixtures — see test_measurement.py.)
SOLID_DECODE_FACTOR = 2.0
# Wall-time sanity ceiling for --mode full. No committed wall_time.json: cold-pass
# ci ratios were misleading, and shared-runner noise makes ratio regression gates
# flake. VISION ≤1.3× / ~2× is informational on realistic full runs / nightly.
WALL_RATIO_BUDGET = 10.0
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


def _accel_config(*, enabled: bool) -> ArchiveyConfig:
    """Force rapidgzip / indexed-bzip2 (via rapidgzip's IndexedBzip2File) on or off.

    ``ON`` engages the accelerator even without ``MemberStreams.SEEKABLE`` (AUTO would
    not). The bzip2 accelerator is rapidgzip's bundled decoder, not the separate
    ``indexed_bzip2`` package — see codecs.py.
    """
    mode = AcceleratorMode.ON if enabled else AcceleratorMode.OFF
    return ArchiveyConfig(use_rapidgzip=mode, use_indexed_bzip2=mode)


def _op_read_all(
    path: Path,
    *,
    config: ArchiveyConfig | None = None,
    member_streams: MemberStreams = MemberStreams(0),
) -> tuple[int, int, int]:
    with enable_measurement():
        with open_archive(path, config=config, member_streams=member_streams) as reader:
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


def _op_read_all_unmeasured(
    path: Path,
    *,
    config: ArchiveyConfig | None = None,
    member_streams: MemberStreams = MemberStreams(0),
) -> None:
    """Read every member without measurement wrappers — fair wall-time peer."""
    with open_archive(path, config=config, member_streams=member_streams) as reader:
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


def _stdlib_targz_read_all(path: Path) -> None:
    with tarfile.open(path, "r:gz") as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            f = tf.extractfile(member)
            if f is not None:
                f.read()


def _stdlib_tarbz2_read_all(path: Path) -> None:
    with tarfile.open(path, "r:bz2") as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            f = tf.extractfile(member)
            if f is not None:
                f.read()


def _rapidgzip_available() -> bool:
    try:
        import rapidgzip  # noqa: F401

        return True
    except ImportError:
        return False


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
        std_fn: Callable[[], None],
        path: Path,
        *,
        config: ArchiveyConfig | None = None,
        member_streams: MemberStreams = MemberStreams(0),
    ) -> tuple[float, float]:
        def ay() -> None:
            _op_read_all_unmeasured(path, config=config, member_streams=member_streams)

        if warmup:
            ay_wall, _ignored, std_wall = _interleaved_pair_times(
                ay,
                std_fn,
                rounds=7,
            )
            return ay_wall, std_wall
        wall, _ = timed_with_optional_warmup(ay)
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

    # --- .tar.gz / .tar.bz2 with accelerators off vs on ---
    # Default AUTO leaves accelerators off unless SEEKABLE is declared; we force ON/OFF
    # explicitly. bzip2 accelerator = rapidgzip.IndexedBzip2File (not indexed_bzip2 pkg).
    has_accel = _rapidgzip_available()
    for label, path, std_fn, fmt in (
        ("targz", fixtures.targz_path, _stdlib_targz_read_all, "tar.gz"),
        ("tarbz2", fixtures.tarbz2_path, _stdlib_tarbz2_read_all, "tar.bz2"),
    ):
        cfg_off = _accel_config(enabled=False)
        _m_wall, (bdec, seeks, unpacked) = timed_with_optional_warmup(
            lambda p=path, c=cfg_off: _op_read_all(p, config=c)
        )
        wall_off, std_wall = pair_wall(
            lambda p=path, s=std_fn: s(p), path, config=cfg_off
        )
        results.append(
            CaseResult(
                f"{label}_read_all_accel_off",
                fmt,
                "read_all",
                wall_off,
                bdec,
                seeks,
                unpacked_bytes=unpacked,
                stdlib_wall_s=std_wall,
                wall_ratio=(wall_off / std_wall) if std_wall > 0 else None,
                notes="stdlib gzip/bz2 codec; accelerators forced OFF",
            )
        )
        if not has_accel:
            results.append(
                CaseResult(
                    f"{label}_read_all_accel_on",
                    fmt,
                    "read_all",
                    0.0,
                    0,
                    0,
                    unpacked_bytes=unpacked,
                    notes="skipped: rapidgzip not installed ([seekable] extra)",
                )
            )
            continue
        cfg_on = _accel_config(enabled=True)
        # SEEKABLE so AUTO would also engage; ON engages either way. Matches the
        # intended accelerator use case (indexed / parallel decode).
        seekable = MemberStreams.SEEKABLE
        _m_wall, (bdec, seeks, unpacked) = timed_with_optional_warmup(
            lambda p=path, c=cfg_on: _op_read_all(p, config=c, member_streams=seekable)
        )
        wall_on, std_wall_on = pair_wall(
            lambda p=path, s=std_fn: s(p),
            path,
            config=cfg_on,
            member_streams=seekable,
        )
        speedup = (wall_off / wall_on) if wall_on > 0 else None
        speedup_note = f"; vs accel_off {speedup:.2f}×" if speedup is not None else ""
        results.append(
            CaseResult(
                f"{label}_read_all_accel_on",
                fmt,
                "read_all",
                wall_on,
                bdec,
                seeks,
                unpacked_bytes=unpacked,
                stdlib_wall_s=std_wall_on,
                wall_ratio=(wall_on / std_wall_on) if std_wall_on > 0 else None,
                notes=f"rapidgzip accelerator ON{speedup_note}",
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
            # Under-decode guard only. For zip/tar/gzip this equals unpacked by
            # construction (counted at member output) — silent *re*-decode of the
            # source is caught by the seek-count baseline below, not this axis.
            # Solid sequential (above) is where the byte axis does real work.
            if (
                r.format in ("zip", "tar", "gzip", "tar.gz", "tar.bz2")
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
    *,
    enforce_vision: bool = False,
) -> list[str]:
    """Sanity / informational wall checks — no committed wall-time baseline."""
    failures: list[str] = []
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
    return failures


def write_baselines(results: list[CaseResult]) -> None:
    """Rewrite the committed structural (seek/bytes) baseline only."""
    BASELINES_DIR.mkdir(parents=True, exist_ok=True)
    structural: dict[str, Any] = {
        "solid_decode_factor": SOLID_DECODE_FACTOR,
        "cases": {},
    }
    for r in results:
        structural["cases"][r.case] = {
            "bytes_decompressed": r.bytes_decompressed,
            "source_seek_count": r.source_seek_count,
            "unpacked_bytes": r.unpacked_bytes,
            "notes": r.notes,
        }
    STRUCTURAL_BASELINE.write_text(json.dumps(structural, indent=2) + "\n")


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _fmt_seconds(seconds: float) -> str:
    if seconds <= 0:
        return "—"
    if seconds < 1.0:
        return f"{seconds * 1000:.1f} ms"
    return f"{seconds:.3f} s"


def _fmt_bytes(n: int | None) -> str:
    if n is None:
        return "—"
    if n < 1024:
        return str(n)
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KiB"
    return f"{n / (1024 * 1024):.2f} MiB"


def _vision_label(ratio: float | None) -> str:
    if ratio is None:
        return "—"
    if ratio <= WALL_RATIO_VISION:
        return f"within ≤{WALL_RATIO_VISION}×"
    if ratio <= WALL_RATIO_VISION_SAFETY:
        return f"above {WALL_RATIO_VISION}×, under {WALL_RATIO_VISION_SAFETY}×"
    return f"above {WALL_RATIO_VISION_SAFETY}× safety"


def format_text_report(payload: dict[str, Any]) -> str:
    """Render a human-friendly markdown report from harness JSON payload."""
    results = [
        CaseResult(**r) if isinstance(r, dict) else r for r in payload["results"]
    ]
    scale = payload.get("scale", "?")
    detail = payload.get("scale_detail") or {}
    lines: list[str] = [
        "# Benchmark report",
        "",
        f"- **mode:** `{payload.get('mode', '?')}`",
        f"- **scale:** `{scale}`",
        f"- **warmup:** `{payload.get('warmup', False)}`",
    ]
    if detail:
        lines.extend(
            [
                "",
                "## Corpus",
                "",
                f"- common members: {detail.get('common_members', '?')} × "
                f"{_fmt_bytes(detail.get('common_member_size'))}",
                f"- gzip size: {_fmt_bytes(detail.get('gzip_size'))}",
                f"- solid members: {detail.get('solid_members', '?')} × "
                f"{_fmt_bytes(detail.get('solid_member_size'))}",
            ]
        )

    wall_cases = [r for r in results if r.wall_ratio is not None]
    other_cases = [r for r in results if r.wall_ratio is None]

    if wall_cases:
        lines.extend(
            [
                "",
                "## Wall-time vs stdlib",
                "",
                "| Case | archivey | stdlib | ratio | vs VISION |",
                "| --- | ---: | ---: | ---: | --- |",
            ]
        )
        for r in wall_cases:
            ratio = f"{r.wall_ratio:.2f}×" if r.wall_ratio is not None else "—"
            lines.append(
                f"| `{r.case}` | {_fmt_seconds(r.wall_s)} | "
                f"{_fmt_seconds(r.stdlib_wall_s or 0.0)} | {ratio} | "
                f"{_vision_label(r.wall_ratio)} |"
            )

    lines.extend(
        [
            "",
            "## All cases",
            "",
            "| Case | format | op | wall | bytes_dec | seeks | notes |",
            "| --- | --- | --- | ---: | ---: | ---: | --- |",
        ]
    )
    for r in results:
        notes = r.notes.replace("|", "\\|") if r.notes else ""
        lines.append(
            f"| `{r.case}` | {r.format} | {r.operation} | "
            f"{_fmt_seconds(r.wall_s)} | {_fmt_bytes(r.bytes_decompressed)} | "
            f"{r.source_seek_count} | {notes} |"
        )

    if other_cases and any(r.case.endswith("_sequential") for r in other_cases):
        lines.extend(["", "## Solid decode notes", ""])
        for r in results:
            if not r.case.endswith(("_sequential", "_random")):
                continue
            unpacked = r.unpacked_bytes or 0
            factor = (r.bytes_decompressed / unpacked) if unpacked else None
            factor_s = f"{factor:.2f}× unpacked" if factor is not None else "—"
            lines.append(
                f"- `{r.case}`: bytes_decompressed={_fmt_bytes(r.bytes_decompressed)} "
                f"({factor_s}); seeks={r.source_seek_count}"
            )

    lines.extend(
        [
            "",
            "## Policy reminder",
            "",
            f"- VISION target ≤{WALL_RATIO_VISION}× stdlib on common paths "
            f"(~{WALL_RATIO_VISION_SAFETY}× safety band is informational on nightly).",
            f"- Sanity ceiling {WALL_RATIO_BUDGET:.0f}× fails the job; VISION band "
            "jitter is printed, not failed.",
            "- Structural seek/bytes gates live on the PR path (`ci.yml`), not here.",
            "",
        ]
    )
    return "\n".join(lines)


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
        help="Rewrite benchmarks/baselines/structural.json from this run (ci scale only)",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Write full results JSON to this path",
    )
    parser.add_argument(
        "--text-out",
        type=Path,
        default=None,
        help="Write a human-friendly markdown report to this path",
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
    report = format_text_report(payload)
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, indent=2) + "\n")
    if args.text_out is not None:
        args.text_out.parent.mkdir(parents=True, exist_ok=True)
        args.text_out.write_text(report)

    # Friendly table first (CI logs / terminals); full JSON still available via --json-out.
    print(report)
    if args.json_out is None:
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
        # Sanity ceiling only — no committed wall_time.json (shared-runner noise).
        # VISION ≤1.3× / ~2× on realistic corpora is informational drift signal.
        for f in _wall_checks(results, enforce_vision=False):
            failures.append(f)
        if args.scale == "realistic":
            for f in _wall_checks(results, enforce_vision=True):
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
