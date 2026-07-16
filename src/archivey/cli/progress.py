"""Lazy tqdm progress helper (optional ``[cli]`` extra)."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable
from typing import Any, TextIO

from archivey.internal.extraction_types import ExtractionProgress


def _load_tqdm() -> Any | None:
    """Import tqdm at call time so a core-only install never loads it at import time."""
    try:
        return importlib.import_module("tqdm").tqdm
    except ImportError:
        return None


def _display_stream(preferred: TextIO) -> TextIO | None:
    """Pick a stream that can show an interactive progress bar.

    Prefer ``preferred`` when it is a TTY. If it has been redirected, fall back to
    ``sys.__stderr__`` when that is still a console (some runners wrap ``sys.stderr``
    while leaving the real console usable).
    """
    if preferred.isatty():
        return preferred
    fallback = getattr(sys, "__stderr__", None)
    if fallback is not None and fallback is not preferred and fallback.isatty():
        return fallback
    return None


def make_progress_callback(
    *,
    hide_progress: bool,
    stream: TextIO | None = None,
) -> Callable[[ExtractionProgress], None] | None:
    """Return an ``on_progress`` callback, or ``None`` when progress should be suppressed.

    Imports ``tqdm`` lazily so a core-only install never loads it.
    """
    if hide_progress:
        return None

    out = stream if stream is not None else sys.stderr
    display = _display_stream(out)
    if display is None:
        return None

    tqdm = _load_tqdm()
    if tqdm is None:
        return None

    bar: Any = None

    def on_progress(progress: ExtractionProgress) -> None:
        nonlocal bar
        total = progress.total_bytes_estimated
        if bar is None:
            # mininterval=0 / miniters=1: small archives finish in one callback; the
            # default 0.1s throttle would hide the bar entirely.
            bar = tqdm(
                total=total,
                unit="B",
                unit_scale=True,
                unit_divisor=1024,
                file=display,
                leave=True,
                desc=progress.member.name,
                mininterval=0,
                miniters=1,
                dynamic_ncols=True,
                disable=False,
            )
        if bar.desc != progress.member.name:
            bar.set_description(progress.member.name, refresh=False)
        if total is not None and bar.total != total:
            bar.total = total
        delta = progress.bytes_written - int(bar.n)
        if delta > 0:
            bar.update(delta)
        else:
            bar.refresh()
        if (
            progress.members_total is not None
            and progress.members_done >= progress.members_total
        ):
            bar.close()

    return on_progress
