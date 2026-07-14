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
