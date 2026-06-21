"""``SlicingStream`` — a bounded view over a region of another binary stream.

Used to present a member's byte range inside a container as a standalone stream, and (via
``fix_stream_start_position``) to give a mid-positioned stream a clean ``tell() == 0``
origin for codec libraries that assume it.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING, BinaryIO

from archivey.internal.streams.binaryio import is_seekable

if TYPE_CHECKING:
    from _typeshed import WriteableBuffer


class SlicingStream(io.RawIOBase, BinaryIO):
    """A view over ``[start, start+length)`` of an underlying binary stream.

    Seekable underlying stream:
      - ``start`` is the absolute offset where the slice begins (default: the stream's
        current position).
      - ``length`` caps the slice (default: to the end of the underlying stream).
      - Seeking is relative to the start of the slice.

    Non-seekable underlying stream:
      - ``start`` must be ``None`` (the slice begins at the current position).
      - ``length`` caps how many bytes may be read; seeking is unsupported.
    """

    def __init__(
        self,
        stream: BinaryIO,
        start: int | None = None,
        length: int | None = None,
    ) -> None:
        super().__init__()
        self._stream = stream
        self._seekable = is_seekable(stream)

        if self._seekable:
            initial_pos = stream.tell()
            if start is None:
                start = initial_pos
            if initial_pos != start:
                stream.seek(start)
        elif start is not None:
            raise ValueError("Cannot slice a non-seekable stream with a start position")

        self._start = start  # absolute start in the underlying stream (seekable only)
        self._length = length
        self._pos = 0  # position relative to the start of the slice

    def _compute_bytes_to_read(self, n: int) -> int:
        if self._length is not None:
            remaining = self._length - self._pos
            if n < 0:
                return max(remaining, 0)
            return min(n, max(remaining, 0))
        return n

    def read(self, n: int = -1, /) -> bytes:
        n = self._compute_bytes_to_read(n)
        if n == 0:
            return b""
        data = self._stream.read(n)
        self._pos += len(data)
        return data

    def readinto(self, b: "WriteableBuffer", /) -> int:
        mv = memoryview(b).cast("B")
        data = self.read(len(mv))
        mv[: len(data)] = data
        return len(data)

    def tell(self, /) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        if not self._seekable:
            raise io.UnsupportedOperation("seek on non-seekable stream")

        assert self._start is not None  # always set for seekable streams
        start_abs = self._start
        current_abs = start_abs + self._pos

        if whence == io.SEEK_SET:
            new_relative = offset
        elif whence == io.SEEK_CUR:
            new_relative = self._pos + offset
        elif whence == io.SEEK_END:
            if self._length is None:
                if offset != 0:
                    raise io.UnsupportedOperation(
                        "SEEK_END is not supported when slice length is not defined "
                        "and offset is non-zero"
                    )
                underlying_end = self._stream.seek(0, io.SEEK_END)
                self._stream.seek(current_abs)  # restore for the caller's mental model
                new_relative = underlying_end - start_abs
            else:
                new_relative = self._length + offset
        else:
            raise ValueError(f"Invalid whence: {whence}")

        if new_relative < 0:
            raise ValueError("Negative seek position")

        # Seeking past a defined end is allowed (reads clamp to empty), matching BytesIO.
        self._stream.seek(start_abs + new_relative)
        self._pos = new_relative
        return self._pos

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    def seekable(self) -> bool:
        return self._seekable


def fix_stream_start_position(stream: BinaryIO) -> BinaryIO:
    """Make a stream behave as if its current position were 0.

    If ``stream`` is seekable and already at offset 0, it is returned unchanged. If it is
    seekable but positioned mid-stream, it is wrapped in a :class:`SlicingStream` so the
    consumer (a codec library that assumes ``tell() == 0``) sees a clean origin.
    Non-seekable streams are returned as-is (the caller can only read forward anyway).
    """
    if not is_seekable(stream):
        return stream
    start_pos = stream.tell()
    if start_pos == 0:
        return stream
    return SlicingStream(stream, start=start_pos)
