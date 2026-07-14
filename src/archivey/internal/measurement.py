"""Opt-in performance measurement for the benchmark harness.

Disabled by default: when off, readers install no wrappers and counters stay at zero
(zero overhead on the hot path). The harness enables measurement via
:func:`enable_measurement` around ``open_archive`` calls.

Counters live on :class:`~archivey.internal.base_reader.BaseArchiveReader` only — not on
the public :class:`~archivey.reader.ArchiveReader` ABC (no public performance API).
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

_ENABLED: ContextVar[bool] = ContextVar("archivey_measurement_enabled", default=False)


def measurement_enabled() -> bool:
    """Return whether the current context requested performance counters."""
    return _ENABLED.get()


@contextmanager
def enable_measurement() -> Iterator[None]:
    """Enable bytes-decompressed / seek counters for archives opened in this context."""
    token = _ENABLED.set(True)
    try:
        yield
    finally:
        _ENABLED.reset(token)


class ByteCounter:
    """Mutable cumulative byte count shared by one or more stream wrappers."""

    __slots__ = ("_total",)

    def __init__(self) -> None:
        self._total = 0

    @property
    def total(self) -> int:
        return self._total

    def add(self, n: int) -> None:
        if n:
            self._total += n

    def reset(self) -> None:
        self._total = 0


class SeekCounter:
    """Mutable count of ``seek`` calls on instrumented source streams."""

    __slots__ = ("_count",)

    def __init__(self) -> None:
        self._count = 0

    @property
    def count(self) -> int:
        return self._count

    def record(self) -> None:
        self._count += 1

    def reset(self) -> None:
        self._count = 0
