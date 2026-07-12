"""Coverage-guided Atheris fuzz harness (threat-model O5 / testing-contract).

Not collected by the default pytest matrix. Run via::

    uv sync --group fuzz --group dev --extra all
    uv run --no-sync python -m tests.atheris_fuzz --smoke
"""

from __future__ import annotations

__all__ = ["TARGET_NAMES", "DEFAULT_BUDGETS"]

# Default main-push partition (~150s). Overridable via ARCHIVEY_FUZZ_BUDGET_<NAME>.
DEFAULT_BUDGETS: dict[str, int] = {
    "sevenzip_header": 45,
    "sevenzip_open": 20,
    "detect_format": 12,
    "zip_tar": 12,
    "iso": 8,
    "rar_header": 30,
    "rar": 15,  # open+list once native RAR is registered
}

TARGET_NAMES: tuple[str, ...] = tuple(DEFAULT_BUDGETS)
