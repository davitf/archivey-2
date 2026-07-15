"""Lazy tqdm progress helper (optional ``[cli]`` extra)."""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import TextIO

from archivey.internal.extraction_types import ExtractionProgress


def make_progress_callback(
    *,
    hide_progress: bool,
    stream: TextIO | None = None,
) -> Callable[[ExtractionProgress], None] | None:
    """Return an ``on_progress`` callback, or ``None`` when progress should be suppressed.

    Imports ``tqdm`` lazily so a core-only install never loads it.
    """
    out = stream if stream is not None else sys.stderr
    if hide_progress or not out.isatty():
        return None
    try:
        from tqdm import tqdm
    except ImportError:
        return None

    bar: tqdm | None = None  # type: ignore[name-defined]

    def on_progress(progress: ExtractionProgress) -> None:
        nonlocal bar
        total = progress.total_bytes_estimated
        if bar is None:
            bar = tqdm(
                total=total,
                unit="B",
                unit_scale=True,
                unit_divisor=1024,
                file=out,
                leave=True,
                desc=progress.member.name,
            )
        # Update description when the current member changes.
        if bar.desc != progress.member.name:
            bar.set_description(progress.member.name, refresh=False)
        if total is not None and bar.total != total:
            bar.total = total
            bar.refresh()
        # Drive the bar from cumulative bytes_written.
        delta = progress.bytes_written - bar.n
        if delta > 0:
            bar.update(delta)
        if (
            progress.members_total is not None
            and progress.members_done >= progress.members_total
        ):
            bar.close()

    return on_progress
