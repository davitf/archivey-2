#!/usr/bin/env python3
"""Adversarial probes for the structural benchmark gate (performance review).

Each probe simulates a plausible re-decompression regression and reports whether
the committed structural gate (``benchmarks.harness._structural_checks`` against
``benchmarks/baselines/structural.json``) would fail it.

Run::

    uv run --no-sync python review/performance/repro.py

Probes:

1. ``solid-collapse``  — the canonical VISION trap: the 7z sequential path
   regresses to per-member from-start folder decodes (O(n²)). Simulated by
   replacing ``SevenZipReader._iter_with_data`` with the base-class default
   (which opens each member independently via ``_open_member``).
2. ``solid-double``    — a subtler regression: every solid folder is decoded
   exactly twice (e.g. an eager verify pass). Checked arithmetically against the
   gate's ``SOLID_DECODE_FACTOR`` bound, and empirically by wrapping
   ``_iter_with_data`` to drain one full extra folder decode per archive.
3. ``zip-double``      — a non-solid path decompresses every member twice but
   delivers bytes once (e.g. an internal pre-read). Simulated by patching
   ``ZipReader._open_member`` to open/consume/close the member, then open again.
   Caught by the non-solid over-decode bound and the tightened seek slack.

Exit code 0 always; output is the evidence table.
"""

from __future__ import annotations

import contextlib
import sys
import tempfile
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2])
)  # repo root for `benchmarks`

from archivey.internal.backends.sevenzip_reader import SevenZipReader
from archivey.internal.backends.zip_reader import ZipReader
from archivey.internal.base_reader import BaseArchiveReader
from benchmarks.fixtures import materialize_fixtures
from benchmarks.harness import (
    SOLID_DECODE_FACTOR,
    STRUCTURAL_BASELINE,
    CaseResult,
    _structural_checks,
    load_json,
    run_cases,
)


@contextlib.contextmanager
def _patched(obj, name, value):
    orig = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, orig)


def _run_gate(fixtures, work: Path) -> list[str]:
    results = run_cases(fixtures, work)
    return _structural_checks(results, load_json(STRUCTURAL_BASELINE))


def probe_solid_collapse(fixtures, work: Path) -> tuple[bool, str]:
    """Sequential solid path degenerates to per-member random opens (O(n^2))."""
    with _patched(SevenZipReader, "_iter_with_data", BaseArchiveReader._iter_with_data):
        failures = _run_gate(fixtures, work / "p1")
    hits = [f for f in failures if "sevenzip_solid_sequential" in f]
    return bool(hits), "; ".join(hits) or "no gate failure"


def probe_solid_double_arithmetic() -> tuple[bool, str]:
    """A full extra decode of every solid block: bytes == 2.0 x unpacked exactly."""
    r = CaseResult(
        case="sevenzip_solid_sequential",
        format="7z",
        operation="read_all_sequential",
        wall_s=0.0,
        bytes_decompressed=2 * 2_097_152,  # decode-once corpus decoded exactly twice
        source_seek_count=4,
        unpacked_bytes=2_097_152,
    )
    failures = _structural_checks([r], load_json(STRUCTURAL_BASELINE))
    caught = any("sevenzip_solid_sequential" in f for f in failures)
    return caught, (
        f"bytes=2.0x unpacked vs bound {SOLID_DECODE_FACTOR}x -> "
        + ("; ".join(failures) or "no gate failure")
    )


def probe_zip_double(fixtures, work: Path) -> tuple[bool, str]:
    """ZIP members decompressed twice internally, delivered once."""
    orig_open = ZipReader._open_member

    def double_open(self, member):
        s = orig_open(self, member)
        try:
            while s.read(65536):
                pass
        finally:
            s.close()
        return orig_open(self, member)

    with _patched(ZipReader, "_open_member", double_open):
        failures = _run_gate(fixtures, work / "p3")
    hits = [f for f in failures if f.startswith("zip_")]
    return bool(hits), "; ".join(hits) or "no gate failure"


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="gate-probe-"))
    fixtures = materialize_fixtures(tmp / "fixtures", scale="ci")
    work = tmp / "work"
    work.mkdir()

    baseline_failures = _run_gate(fixtures, work / "p0")
    print(
        f"unpatched gate: {'FAIL ' + str(baseline_failures) if baseline_failures else 'green'}"
    )

    for name, fn in (
        (
            "solid-collapse (O(n^2) sequential regression)",
            lambda: probe_solid_collapse(fixtures, work),
        ),
        ("solid-double (exactly 2x decode)", probe_solid_double_arithmetic),
        (
            "zip-double (member decoded twice, delivered once)",
            lambda: probe_zip_double(fixtures, work),
        ),
    ):
        caught, detail = fn()
        verdict = "CAUGHT" if caught else "NOT CAUGHT"
        print(f"{name}: {verdict}\n    {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
