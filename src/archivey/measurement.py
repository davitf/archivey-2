"""Public measurement / IO-stats API.

Enable performance counters by wrapping ``open_archive()`` in
:func:`enable_measurement`::

    import archivey
    from archivey.measurement import enable_measurement

    with enable_measurement():
        with archivey.open_archive("data.zip") as reader:
            data = reader.read("file.txt")
            stats = reader.io_stats()
            if stats:
                print(stats.bytes_decompressed)

Counters stay at zero (and :meth:`~archivey.ArchiveReader.io_stats` returns
``None``) unless the reader was opened inside an :func:`enable_measurement`
context — zero overhead on the hot path when measurement is off.
"""

from __future__ import annotations

from dataclasses import dataclass

from archivey.internal.measurement import enable_measurement

__all__ = ["IoStats", "enable_measurement"]


@dataclass(frozen=True)
class IoStats:
    """I/O counters sampled from an archive reader with measurement enabled.

    Returned by :meth:`~archivey.ArchiveReader.io_stats`; ``None`` when the reader
    was not opened inside :func:`enable_measurement`.
    """

    bytes_decompressed: int
    """Total decoded / output bytes delivered through member streams so far."""

    compressed_bytes_consumed: int | None
    """Compressed bytes pulled from the archive's outer source so far, or ``None``
    when the source size is statically known (the static ratio is used instead)."""

    source_seek_count: int
    """Number of ``seek()`` calls on the instrumented archive source."""
