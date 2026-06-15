"""Stream that exposes a bounded slice of an underlying stream."""

import io
from typing import BinaryIO

from archivey.internal.streams.compat import is_seekable
from archivey.internal.utils import ensure_not_none


class SlicingStream(io.RawIOBase, BinaryIO):
    def __init__(
        self, stream: BinaryIO, start: int | None = None, length: int | None = None
    ):
        """
        Wraps a binary stream to provide a view (slice) of a portion of it.

        If the underlying stream `stream` is seekable:
        - If `start` is provided, it defines the absolute offset in the underlying
          stream where the slice begins. If `start` is None, the slice begins
          at the underlying stream's current position.
        - If `length` is provided, it defines the maximum number of bytes in the
          slice. If `length` is None, the slice extends to the end of the
          underlying stream.
        - Seeking within this stream will be relative to the start of the slice.

        If the underlying stream `stream` is not seekable:
        - `start` must be None (or not provided), as seeking to an absolute
          position is not possible. The slice implicitly starts from the current
          position of the non-seekable stream.
        - If `length` is provided, it defines the maximum number of bytes that
          can be read from the slice. If `length` is None, the slice will
          read until the underlying non-seekable stream is exhausted.
        - Seeking is not supported.

        Args:
            stream: The underlying binary IO stream.
            start: The absolute starting position of the slice in the underlying
                   stream. If None and stream is seekable, uses current position.
                   Must be None if stream is not seekable.
            length: The maximum length of the slice. If None, reads until the end
                    of the underlying stream (or until the non-seekable stream ends).
        """
        super().__init__()
        self._stream = stream
        self._seekable = is_seekable(stream)
        self._initial_stream_pos: int | None = None

        if self._seekable:
            self._initial_stream_pos = stream.tell()
            if start is None:
                start = self._initial_stream_pos
            # Position the underlying stream at the start of the slice
            if self._initial_stream_pos != start:
                stream.seek(start)
        else:
            if start is not None:
                raise ValueError(
                    "Cannot slice a non-seekable stream with a start position"
                )
            # For non-seekable streams, start is implicitly the current position.
            # We don't store it as it's not an absolute position we can return to.

        self._start = start  # Absolute start in the underlying stream if seekable
        self._length = length
        self._pos = 0  # Current position relative to the start of the slice

    def _compute_bytes_to_read(self, n: int) -> int:
        if self._length is not None:
            remaining = self._length - self._pos
            if n == -1:
                return remaining
            return min(n, remaining)
        return n

    def read(self, n: int = -1) -> bytes:
        n = self._compute_bytes_to_read(n)
        if n == 0:
            return b""

        data = self._stream.read(n)
        self._pos += len(data)
        return data

    def readinto(self, b: bytearray | memoryview) -> int:  # type: ignore[override]
        buf = self.read(len(b))
        b[: len(buf)] = buf
        return len(buf)

    def tell(self) -> int:
        """Return the current position within the slice."""
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        """
        Change the stream position within the current slice.

        Args:
            offset: The offset in bytes.
            whence: The reference point for the offset.
                io.SEEK_SET (0): Start of the slice.
                io.SEEK_CUR (1): Current position within the slice.
                io.SEEK_END (2): End of the slice (if length is defined).

        Returns:
            The new absolute position within the slice.

        Raises:
            ValueError: If whence is invalid.
            io.UnsupportedOperation: If the stream is not seekable, or if trying
                                     to seek outside slice boundaries in some cases.
        """
        if not self._seekable:
            raise io.UnsupportedOperation("seek on non-seekable stream")

        start_abs = ensure_not_none(self._start)
        current_abs_pos_in_stream = start_abs + self._pos
        new_relative_pos: int

        if whence == io.SEEK_SET:
            new_relative_pos = offset
        elif whence == io.SEEK_CUR:
            new_relative_pos = self._pos + offset
        elif whence == io.SEEK_END:
            if self._length is None:
                # Seeking from SEEK_END is problematic if length is not defined.
                # We could try to seek to the end of the underlying stream,
                # but that might be very far.
                # For now, let's disallow SEEK_END if length is not set.
                # Alternatively, one could argue it should behave like underlying stream's SEEK_END.
                # However, the slice abstraction implies boundaries.
                # Let underlying stream handle if offset is 0, effectively asking for its size.
                if offset == 0:
                    # This will effectively give the size of the underlying stream
                    # relative to our start, which can act as an unbounded length.
                    # We don't set self._length here, but it informs the possible _pos.
                    underlying_end = self._stream.seek(0, io.SEEK_END)
                    self._stream.seek(current_abs_pos_in_stream)  # restore position
                    new_relative_pos = underlying_end - start_abs + offset

                else:
                    raise io.UnsupportedOperation(
                        "SEEK_END is not supported when slice length is not defined "
                        "and offset is non-zero"
                    )

            else:
                new_relative_pos = self._length + offset
        else:
            raise ValueError(f"Invalid whence: {whence}")

        if new_relative_pos < 0:
            raise ValueError("Negative seek position")

        if self._length is not None and new_relative_pos > self._length:
            # Allow seeking past the defined end, but reads will be clamped.
            # This matches behavior of io.BytesIO.
            pass

        # Calculate the new absolute position in the underlying stream
        new_abs_pos_in_stream = start_abs + new_relative_pos

        # Perform the actual seek on the underlying stream
        self._stream.seek(new_abs_pos_in_stream)
        self._pos = new_relative_pos
        return self._pos

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    def seekable(self) -> bool:
        return self._seekable
