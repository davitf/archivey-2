"""CLI logging setup (library loggers → stderr)."""

from __future__ import annotations

import logging
import sys
from typing import TextIO

_HANDLER: logging.StreamHandler[TextIO] | None = None


def configure_cli_logging(*, verbose: bool, err: TextIO | None = None) -> None:
    """Install a stderr handler on the ``archivey`` logger tree (D4).

    Default level is WARNING so normalization / diagnostic warnings are visible
    with a real formatter instead of Python's last-resort handler. ``-v`` lowers
    the level to INFO.
    """
    global _HANDLER
    stream = err if err is not None else sys.stderr
    root = logging.getLogger("archivey")
    root.setLevel(logging.INFO if verbose else logging.WARNING)
    root.propagate = False

    if _HANDLER is None:
        handler: logging.StreamHandler[TextIO] = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        root.addHandler(handler)
        _HANDLER = handler
        return

    # Re-point at the caller's err without flushing a possibly-closed prior stream
    # (pytest capsys / BrokenPipe handlers close streams between invocations).
    _HANDLER.stream = stream
