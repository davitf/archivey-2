"""CLI logging setup (library loggers → stderr), scoped to one invocation."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TextIO


@contextmanager
def cli_logging(*, verbose: bool, err: TextIO) -> Iterator[None]:
    """Route the ``archivey`` logger tree to ``err`` for one CLI invocation (D4).

    Default level is WARNING so normalization / diagnostic warnings are visible
    with a real formatter instead of Python's last-resort handler; ``-v`` lowers
    the level to INFO. The handler is installed only for the duration of the
    invocation and ``propagate`` is left untouched: with a handler present the
    last-resort handler never fires, and library records still reach root-level
    handlers (pytest's caplog, an embedding app). Mutating ``propagate`` or
    keeping a process-global handler would leak state into everything else in
    the process — on pytest 8.3 (the supported floor) it silently blinds every
    later caplog-based warning assertion.
    """
    root = logging.getLogger("archivey")
    handler: logging.StreamHandler[TextIO] = logging.StreamHandler(err)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO if verbose else logging.WARNING)
    try:
        yield
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
        handler.close()
