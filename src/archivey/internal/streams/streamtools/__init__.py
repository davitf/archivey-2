"""Generic binary-stream plumbing — adapt, classify, and slice arbitrary ``BinaryIO``.

This subpackage is the codec- and format-agnostic core of the stream layer: it knows
nothing about archivey's error hierarchy or any codec, only about stdlib binary streams.
That independence is deliberate — it could be lifted out as a standalone library — so
nothing here may import from the rest of ``archivey``.

Module map:

- :mod:`.base` — ``ReadOnlyIOStream`` / ``DelegatingStream`` (wrapper bases)
- :mod:`.binaryio` — classify/coerce sources (``is_seekable``, ``ensure_binaryio``, …)
- :mod:`.slice` — ``SlicingStream`` bound view + ``fix_stream_start_position``
- :mod:`.shared` — ``SharedSource`` (concurrent independent views over one handle)
- :mod:`.locked` — ``LockedStream`` / ``CloseLockedStream`` (whole-op lock wrappers)
- :mod:`.solid` — ``SolidBlockReader`` (forward-only solid demux)

When to use which concurrency helper:

- ``LockedStream`` — one shared handle; hold a lock across each seek+read (TAR/ISO).
- ``SharedSource`` + locked ``SlicingStream`` — each consumer has its own logical
  position; every read re-seeks under the lock (ZIP-style shared file).
- ``SolidBlockReader`` — one forward decode; hand out consecutive member slices
  (7z folder / RAR pipe). Not seekable.

Import from this package root rather than the individual modules.
"""

from __future__ import annotations

from archivey.internal.streams.streamtools.base import (
    DelegatingStream,
    ReadOnlyIOStream,
)
from archivey.internal.streams.streamtools.binaryio import (
    BinaryIOWrapper,
    ReadableStream,
    ensure_binaryio,
    ensure_bufferedio,
    is_filename,
    is_seekable,
    is_stream,
    read_exact,
    read_full_count,
    source_byte_size,
    source_name,
)
from archivey.internal.streams.streamtools.locked import CloseLockedStream, LockedStream
from archivey.internal.streams.streamtools.shared import SharedSource
from archivey.internal.streams.streamtools.slice import (
    SlicingStream,
    fix_stream_start_position,
)
from archivey.internal.streams.streamtools.solid import (
    SolidBlockReader,
    skip_forward,
)

__all__ = [
    "BinaryIOWrapper",
    "CloseLockedStream",
    "DelegatingStream",
    "LockedStream",
    "ReadOnlyIOStream",
    "ReadableStream",
    "SharedSource",
    "SlicingStream",
    "SolidBlockReader",
    "ensure_binaryio",
    "ensure_bufferedio",
    "fix_stream_start_position",
    "is_filename",
    "is_seekable",
    "is_stream",
    "read_exact",
    "read_full_count",
    "skip_forward",
    "source_byte_size",
    "source_name",
]
