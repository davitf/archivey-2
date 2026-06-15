"""Backward-compatibility shim.

The real implementations now live in ``archivey.internal.streams.*``. This module
re-exports every public symbol historically imported from this module. Names listed
in ``__all__`` count as explicit re-exports (required under mypy ``--strict``).
"""

from archivey.internal.streams.compat import (
    ALL_IO_METHODS,
    ALL_IO_PROPERTIES,
    BinaryIOWrapper,
    BinaryStreamLike,
    CloseableStream,
    NonClosingBufferedReader,
    ReadableBinaryStream,
    ReadableStreamLikeOrSimilar,
    WritableBinaryStream,
    ensure_binaryio,
    ensure_bufferedio,
    fix_stream_start_position,
    is_filename,
    is_seekable,
    is_stream,
    open_if_file,
    read_exact,
)
from archivey.internal.streams.concat import ConcatenationStream
from archivey.internal.streams.detect import RecordableStream, RewindableStreamWrapper
from archivey.internal.streams.errors import (
    ErrorIOStream,
    ExceptionTranslatorFn,
    run_with_exception_translation,
)
from archivey.internal.streams.slice import SlicingStream
from archivey.internal.streams.stats import IOStats, StatsIO

__all__ = [
    "ALL_IO_METHODS", "ALL_IO_PROPERTIES", "BinaryIOWrapper", "BinaryStreamLike",
    "CloseableStream", "ConcatenationStream", "ErrorIOStream", "ExceptionTranslatorFn",
    "IOStats", "NonClosingBufferedReader", "ReadableBinaryStream",
    "ReadableStreamLikeOrSimilar", "RecordableStream", "RewindableStreamWrapper",
    "SlicingStream", "StatsIO", "WritableBinaryStream", "ensure_binaryio",
    "ensure_bufferedio", "fix_stream_start_position", "is_filename", "is_seekable",
    "is_stream", "open_if_file", "read_exact", "run_with_exception_translation",
]
