"""Generic binary-stream plumbing — adapt, classify, and slice arbitrary ``BinaryIO``.

This subpackage is the codec- and format-agnostic core of the stream layer: it knows
nothing about archivey's error hierarchy or any codec, only about stdlib binary streams.
That independence is deliberate — it could be lifted out as a standalone library — so
nothing here may import from the rest of ``archivey``.

The public surface is re-exported below; import from this package root rather than the
individual modules.
"""

from __future__ import annotations

from archivey.internal.streams.streamtools.binaryio import (
    BinaryIOWrapper,
    ReadableStream,
    ensure_binaryio,
    ensure_bufferedio,
    is_filename,
    is_seekable,
    is_stream,
    read_exact,
)
from archivey.internal.streams.streamtools.slice import (
    SlicingStream,
    fix_stream_start_position,
)

__all__ = [
    "BinaryIOWrapper",
    "ReadableStream",
    "SlicingStream",
    "ensure_binaryio",
    "ensure_bufferedio",
    "fix_stream_start_position",
    "is_filename",
    "is_seekable",
    "is_stream",
    "read_exact",
]
