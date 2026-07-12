"""Coverage-guided Atheris fuzz harness (threat-model O5 / testing-contract).

Not collected by the default pytest matrix. Run via::

    uv sync --group fuzz --group dev --extra all
    uv run --no-sync python -m tests.atheris_fuzz --smoke
"""

from __future__ import annotations

__all__ = ["TARGET_NAMES", "DEFAULT_BUDGETS"]

# Default main-push partition (~120s). Overridable via ARCHIVEY_FUZZ_BUDGET_<NAME>.
DEFAULT_BUDGETS: dict[str, int] = {
    "sevenzip_header": 55,
    "sevenzip_open": 25,
    "detect_format": 15,
    "zip_tar": 15,
    "iso": 10,
    "rar": 0,  # scaffold; skipped until backend registers
}

TARGET_NAMES: tuple[str, ...] = tuple(DEFAULT_BUDGETS)
