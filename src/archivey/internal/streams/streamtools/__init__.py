"""Generic binary-stream plumbing — adapt, classify, and slice arbitrary ``BinaryIO``.

This subpackage is the codec- and format-agnostic core of the stream layer: it knows
nothing about archivey's error hierarchy or any codec, only about stdlib binary streams.
That independence is deliberate — it could be lifted out as a standalone library — so
nothing here may import from the rest of ``archivey``.

The public surface is re-exported below; import from this package root rather than the
individual modules.
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
    "skip_forward",
    "source_byte_size",
    "source_name",
]
