"""Capture the ``open_archive()`` caller stack for capability-error breadcrumbs."""

from __future__ import annotations

import traceback
from dataclasses import dataclass
from types import FrameType


@dataclass(frozen=True)
class OpenSite:
    """Where the caller invoked ``open_archive`` (outside archivey frames)."""

    filename: str
    lineno: int
    # Full stack snapshot retained for diagnostics (unconditional; open-time cost only).
    stack: tuple[traceback.FrameSummary, ...]

    @property
    def location(self) -> str:
        return f"{self.filename}:{self.lineno}"


def capture_open_site(*, skip_module_prefixes: tuple[str, ...] = ("archivey.",)) -> OpenSite:
    """Walk frames until the first non-archivey caller; keep the full stack."""
    stack = tuple(traceback.extract_stack()[:-1])  # drop this helper frame
    frame: FrameType | None = None
    try:
        # Start from the caller of this function.
        frame = __import__("sys")._getframe(1)
    except ValueError:
        frame = None

    filename = "<unknown>"
    lineno = 0
    while frame is not None:
        mod = frame.f_globals.get("__name__", "")
        if not any(mod == p.rstrip(".") or mod.startswith(p) for p in skip_module_prefixes):
            filename = frame.f_code.co_filename
            lineno = frame.f_lineno
            break
        frame = frame.f_back
    else:
        # Fall back to the last non-archivey summary in the extracted stack.
        for summary in reversed(stack):
            # extract_stack has no module name; use path heuristics.
            if "/archivey/" not in summary.filename.replace("\\", "/") and not summary.filename.endswith(
                "open_site.py"
            ):
                filename = summary.filename
                lineno = summary.lineno or 0
                break

    return OpenSite(filename=filename, lineno=lineno, stack=stack)
