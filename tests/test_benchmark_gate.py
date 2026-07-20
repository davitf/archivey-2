"""Pytest entry for the structural benchmark gate (solid no-re-decode + axes)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.fixtures import materialize_fixtures
from benchmarks.harness import (
    STRUCTURAL_BASELINE,
    _structural_checks,
    load_json,
    run_cases,
)


@pytest.mark.timeout(120)
def test_benchmark_structural_gate(tmp_path: Path) -> None:
    """PR gate: solid sequential decode bound + common-path byte counts."""
    fixtures = materialize_fixtures(tmp_path / "fixtures")
    if fixtures.solid_7z is None:
        pytest.importorskip("py7zr")
    work = tmp_path / "work"
    work.mkdir()
    results = run_cases(fixtures, work)
    # Ensure the solid sequential case ran when 7z is available.
    sequential = [r for r in results if r.case == "sevenzip_solid_sequential"]
    assert sequential, "expected sevenzip_solid_sequential case"
    assert sequential[0].bytes_decompressed <= (sequential[0].unpacked_bytes or 0) * 2

    random = [r for r in results if r.case == "sevenzip_solid_random"]
    assert random
    # Random opens re-decode; recorded, not failed — but must be visible.
    assert random[0].bytes_decompressed >= sequential[0].bytes_decompressed

    failures = _structural_checks(results, load_json(STRUCTURAL_BASELINE))
    assert not failures, "structural gate failures:\n" + "\n".join(failures)


def test_structural_baseline_committed() -> None:
    assert STRUCTURAL_BASELINE.is_file()
    data = json.loads(STRUCTURAL_BASELINE.read_text())
    assert "sevenzip_solid_sequential" in data["cases"]


def test_format_text_report_table() -> None:
    """Friendly markdown report is readable without downloading JSON."""
    from benchmarks.harness import format_text_report

    payload = {
        "mode": "full",
        "scale": "realistic",
        "warmup": True,
        "scale_detail": {
            "common_members": 64,
            "common_member_size": 262144,
            "gzip_size": 33554432,
            "solid_members": 64,
            "solid_member_size": 262144,
        },
        "results": [
            {
                "case": "zip_read_all",
                "format": "zip",
                "operation": "read_all",
                "wall_s": 0.0158,
                "bytes_decompressed": 1_048_576,
                "source_seek_count": 3,
                "unpacked_bytes": 1_048_576,
                "stdlib_wall_s": 0.0134,
                "wall_ratio": 1.18,
                "notes": "",
            },
            {
                "case": "sevenzip_solid_sequential",
                "format": "7z",
                "operation": "read_all_sequential",
                "wall_s": 0.4,
                "bytes_decompressed": 1_048_576,
                "source_seek_count": 2,
                "unpacked_bytes": 1_048_576,
                "stdlib_wall_s": None,
                "wall_ratio": None,
                "notes": "solid invariant",
            },
        ],
    }
    report = format_text_report(payload)
    assert "# Benchmark report" in report
    assert "| Case | archivey | stdlib | ratio | vs VISION |" in report
    assert "`zip_read_all`" in report
    assert "1.18×" in report
    assert "within ≤1.3×" in report
    assert "solid invariant" in report
    assert "64 × 256.0 KiB" in report or "64 × 256 KiB" in report
    assert "wall-ratio *drift*" in report


def test_wall_drift_checks_regressions_and_noise() -> None:
    """Nightly drift gate: relative+abs dual threshold; seed/missing skipped."""
    from benchmarks.harness import CaseResult, _wall_drift_checks

    def _case(name: str, ratio: float | None) -> CaseResult:
        return CaseResult(
            case=name,
            format="zip",
            operation="read_all",
            wall_s=0.02,
            bytes_decompressed=100,
            source_seek_count=1,
            wall_ratio=ratio,
        )

    previous = {
        "results": [
            {"case": "zip_read_all", "wall_ratio": 1.20},
            {"case": "gzip_read_all", "wall_ratio": 1.05},
            {"case": "no_peer", "wall_ratio": None},
        ]
    }
    # Clear regression: 1.20 → 1.80 (>1.25× and +0.15 abs).
    bad = _wall_drift_checks(
        [_case("zip_read_all", 1.80), _case("gzip_read_all", 1.06)],
        previous,
    )
    assert len(bad) == 1
    assert "zip_read_all" in bad[0]

    # Small bump under both thresholds — OK (noise).
    ok = _wall_drift_checks(
        [_case("zip_read_all", 1.30), _case("gzip_read_all", 1.10)],
        previous,
    )
    assert ok == []

    # Improvement — OK.
    better = _wall_drift_checks([_case("zip_read_all", 1.00)], previous)
    assert better == []

    # No previous / empty — seed.
    assert _wall_drift_checks([_case("zip_read_all", 9.0)], None) == []
    assert _wall_drift_checks([_case("zip_read_all", 9.0)], {"results": []}) == []

    # New case not in previous — skip.
    assert _wall_drift_checks([_case("brand_new_case", 5.0)], previous) == []
