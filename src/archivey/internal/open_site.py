"""Capture the ``open_archive()`` caller location for capability-error breadcrumbs."""

from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass
from types import FrameType


@dataclass(frozen=True)
class OpenSite:
    """Where the caller invoked ``open_archive`` (outside archivey frames).

    Only the ``file:line`` is captured — that is all any consumer reads (the
    ``ConcurrentAccessError`` breadcrumb). We deliberately do NOT retain a full
    ``traceback.extract_stack()`` snapshot: it cost an unconditional stack format on
    every ``open_archive`` and held ``FrameSummary`` objects for the reader's whole
    lifetime, which the founding "open millions of archives" dedupe workload pays for
    with nothing reading it back.
    """

    filename: str
    lineno: int

    @property
    def location(self) -> str:
        return f"{self.filename}:{self.lineno}"


def capture_open_site(
    *, skip_module_prefixes: tuple[str, ...] = ("archivey.",)
) -> OpenSite:
    """Return the first non-archivey caller's ``file:line`` (cheap frame walk only)."""
    frame: FrameType | None = None
    try:
        # Start from the caller of this function.
        frame = sys._getframe(1)
    except ValueError:
        frame = None

    filename = "<unknown>"
    lineno = 0
    while frame is not None:
        mod = frame.f_globals.get("__name__", "")
        if not any(
            mod == p.rstrip(".") or mod.startswith(p) for p in skip_module_prefixes
        ):
            filename = frame.f_code.co_filename
            lineno = frame.f_lineno
            break
        frame = frame.f_back
    else:
        # Frame introspection is unavailable (e.g. a Python without sys._getframe): fall
        # back to the more expensive extracted stack, taken only on this rare path.
        for summary in reversed(traceback.extract_stack()[:-1]):
            # extract_stack has no module name; use path heuristics.
            if "/archivey/" not in summary.filename.replace(
                "\\", "/"
            ) and not summary.filename.endswith("open_site.py"):
                filename = summary.filename
                lineno = summary.lineno or 0
                break

    return OpenSite(filename=filename, lineno=lineno)
