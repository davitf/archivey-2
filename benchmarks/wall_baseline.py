"""Wall-artifact provenance helpers (stdlib only).

Used by ``benchmarks/harness.py`` and by ``benchmark-wall.yml`` on skip
re-publish paths (no ``uv sync`` / archivey import required).

Provenance fields on harness JSON:

- ``measured_at`` — UTC ISO-8601 when ratios were actually timed (Z suffix)
- ``source_run_id`` / ``source_sha`` — Actions identity of that measurement
- ``republished_at`` / ``republish_run_id`` — set when a skip re-uploads the
  same ratios so dormant nightly successes keep an artifact in the recent
  window without pretending a new measurement happened
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Force a full realistic run at least this often even when HEAD is unchanged
# (runner image / CPython patch drift). Keyed off ``measured_at``, never
# artifact upload time — re-publish refreshes upload time daily.
MEASURE_MAX_AGE_DAYS = 30
MEASURE_MAX_AGE_SECONDS = MEASURE_MAX_AGE_DAYS * 86400

ARTIFACT_JSON_NAME = "benchmark-wall-realistic.json"
ARTIFACT_MD_NAME = "benchmark-wall-realistic.md"


def utc_now_iso() -> str:
    """UTC timestamp with ``Z`` suffix, second precision."""
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def parse_utc_iso(value: str) -> datetime | None:
    """Parse harness ``measured_at`` / ``republished_at`` strings."""
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def measured_at_age_seconds(
    payload: dict[str, Any], *, now: datetime | None = None
) -> float | None:
    """Seconds since ``measured_at``, or ``None`` if missing/unparseable."""
    raw = payload.get("measured_at")
    if not isinstance(raw, str):
        return None
    measured = parse_utc_iso(raw)
    if measured is None:
        return None
    current = now or datetime.now(timezone.utc)
    return max(0.0, (current - measured).total_seconds())


def measurement_provenance(
    *,
    run_id: str | None = None,
    sha: str | None = None,
) -> dict[str, str]:
    """Fresh measurement stamps for a full harness run."""
    out: dict[str, str] = {"measured_at": utc_now_iso()}
    rid = run_id if run_id is not None else os.environ.get("GITHUB_RUN_ID")
    commit = sha if sha is not None else os.environ.get("GITHUB_SHA")
    if rid:
        out["source_run_id"] = rid
    if commit:
        out["source_sha"] = commit
    return out


def stamp_republish(
    payload: dict[str, Any],
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Copy payload and mark a skip re-publish; keep original ``measured_at``."""
    out = dict(payload)
    out["republished_at"] = utc_now_iso()
    rid = run_id if run_id is not None else os.environ.get("GITHUB_RUN_ID")
    if rid:
        out["republish_run_id"] = rid
    return out


def wall_ratio_map(payload: dict[str, Any]) -> dict[str, float]:
    """Case name → wall_ratio from a harness JSON payload."""
    out: dict[str, float] = {}
    for row in payload.get("results", []):
        if not isinstance(row, dict):
            continue
        name = row.get("case")
        ratio = row.get("wall_ratio")
        # bool is a subclass of int — reject it explicitly.
        if (
            isinstance(name, str)
            and isinstance(ratio, (int, float))
            and not isinstance(ratio, bool)
        ):
            out[name] = float(ratio)
    return out


def overlapping_wall_ratio_count(
    results: list[Any],
    previous: dict[str, Any],
) -> int:
    """How many current cases have a positive prior ``wall_ratio`` to compare."""
    prior = wall_ratio_map(previous)
    count = 0
    for r in results:
        ratio = getattr(r, "wall_ratio", None)
        if ratio is None:
            continue
        name = getattr(r, "case", None)
        if not isinstance(name, str):
            continue
        old = prior.get(name)
        if old is not None and old > 0:
            count += 1
    return count


def republish_files(
    src_json: Path,
    dest_dir: Path,
    *,
    run_id: str | None = None,
    src_md: Path | None = None,
) -> dict[str, Any]:
    """Stamp re-publish provenance and write JSON (+ optional markdown) into dest."""
    payload = json.loads(src_json.read_text())
    if not isinstance(payload, dict):
        raise SystemExit(f"wall baseline root must be an object: {src_json}")
    stamped = stamp_republish(payload, run_id=run_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_json = dest_dir / ARTIFACT_JSON_NAME
    dest_json.write_text(json.dumps(stamped, indent=2) + "\n")

    md_src = src_md if src_md is not None else src_json.with_name(ARTIFACT_MD_NAME)
    dest_md = dest_dir / ARTIFACT_MD_NAME
    measured = stamped.get("measured_at", "?")
    republished = stamped.get("republished_at", "?")
    header = (
        f"_Re-published without re-measurement "
        f"(measured_at={measured}, republished_at={republished})._\n\n"
    )
    if md_src.is_file():
        body = md_src.read_text()
        if body.startswith("_Re-published without re-measurement"):
            # Drop a previous re-publish banner if present.
            parts = body.split("\n\n", 1)
            body = parts[1] if len(parts) == 2 else body
        dest_md.write_text(header + body)
    else:
        dest_md.write_text(
            header
            + "# Benchmark report\n\n"
            + "(Original markdown missing; JSON baseline re-published.)\n"
        )
    return stamped


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    age = sub.add_parser("age-seconds", help="Print measured_at age in seconds")
    age.add_argument("json_path", type=Path)

    rep = sub.add_parser(
        "republish", help="Stamp and write re-published artifact files"
    )
    rep.add_argument("src_json", type=Path)
    rep.add_argument("dest_dir", type=Path)
    rep.add_argument(
        "--run-id",
        default=None,
        help="Override GITHUB_RUN_ID for republish_run_id",
    )

    args = parser.parse_args(argv)
    if args.cmd == "age-seconds":
        payload = json.loads(args.json_path.read_text())
        seconds = measured_at_age_seconds(payload)
        if seconds is None:
            # Missing provenance → treat as infinitely stale so the workflow
            # forces a full run to stamp measured_at.
            print(MEASURE_MAX_AGE_SECONDS + 1)
        else:
            print(int(seconds))
        return 0
    if args.cmd == "republish":
        stamped = republish_files(args.src_json, args.dest_dir, run_id=args.run_id)
        print(
            f"Re-published baseline measured_at={stamped.get('measured_at')} "
            f"republished_at={stamped.get('republished_at')}",
            file=sys.stderr,
        )
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
