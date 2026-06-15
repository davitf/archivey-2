"""Recordable and rewindable stream wrappers for format detection."""

import io
from typing import BinaryIO

from archivey.internal.streams.compat import ensure_binaryio, is_seekable
from archivey.internal.streams.concat import ConcatenationStream
from archivey.types import ReadableStreamLikeOrSimilar


class RecordableStream(io.RawIOBase, BinaryIO):
    """Wrap a stream, caching all data read from it."""

    def __init__(self, inner: ReadableStreamLikeOrSimilar):
        super().__init__()
        self._inner = inner
        self._buffer = bytearray()
        self._pos = 0
        self._inner_eof = False

    def get_all_data(self) -> bytes:
        """Return all data read so far."""
        return bytes(self._buffer)

    def get_complete_stream(self) -> ConcatenationStream:
        """Return a stream that will provide all the data in the original stream,
        including any data read so far.

        Calling this method closes this stream, to prevent messing up the contents of
        the concatenated stream.
        """
        concatenation = ConcatenationStream([io.BytesIO(self._buffer), self._inner])
        self.close()
        return concatenation

    # Basic IO methods -------------------------------------------------
    def read(self, n: int = -1) -> bytes:
        if self.closed:
            raise ValueError("I/O operation on closed file.")

        if n == -1:
            data = self._buffer[self._pos :]
            self._pos = len(self._buffer)
            chunk = self._inner.read()
            self._buffer.extend(chunk)
            self._pos = len(self._buffer)
            self._inner_eof = True
            return bytes(data) + chunk

        remaining = n
        data = bytearray()

        available = len(self._buffer) - self._pos
        if available > 0:
            take = min(available, remaining)
            data.extend(self._buffer[self._pos : self._pos + take])
            self._pos += take
            remaining -= take

        if remaining > 0 and not self._inner_eof:
            chunk = self._inner.read(remaining)
            if not chunk:
                self._inner_eof = True
            self._buffer.extend(chunk)
            self._pos += len(chunk)
            data.extend(chunk)

        return bytes(data)

    def readinto(self, b: bytearray | memoryview) -> int:  # type: ignore[override]
        data = self.read(len(b))
        n = len(data)
        b[:n] = data
        return n

    # Seek/Tell --------------------------------------------------------
    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_CUR:
            offset = self._pos + offset
        elif whence == io.SEEK_END:
            raise io.UnsupportedOperation("seek to end")
        elif whence != io.SEEK_SET:
            raise ValueError(f"Invalid whence: {whence}")

        if offset < 0:
            raise io.UnsupportedOperation("seek outside recorded region")

        while offset > len(self._buffer):
            chunk = self._inner.read(offset - len(self._buffer))
            if not chunk:
                self._inner_eof = True
                break
            self._buffer.extend(chunk)

        self._pos = offset
        return self._pos

    def tell(self) -> int:
        return self._pos

    # Properties -------------------------------------------------------
    def readable(self) -> bool:  # pragma: no cover - trivial
        return True

    def writable(self) -> bool:  # pragma: no cover - trivial
        return False

    def seekable(self) -> bool:  # pragma: no cover - trivial
        return True

    # Control methods --------------------------------------------------
    def close(self) -> None:  # pragma: no cover - simple delegation
        # Do not close the underlying stream, as it may be used by other code.
        super().close()


class RewindableStreamWrapper:
    def __init__(self, stream: ReadableStreamLikeOrSimilar):
        self._stream = stream
        self._start_pos: int | None = None
        self._recordable_stream: RecordableStream | None = None

        if is_seekable(stream):
            self._start_pos = stream.tell()  # type: ignore[union-attr]
        else:
            self._recordable_stream = RecordableStream(stream)

    def get_stream(self) -> BinaryIO:
        if self._recordable_stream is not None:
            return self._recordable_stream
        return ensure_binaryio(self._stream)

    def get_rewinded_stream(self) -> BinaryIO:
        if self._start_pos is not None:
            self._stream.seek(self._start_pos)  # type: ignore[union-attr]
            return ensure_binaryio(self._stream)

        assert self._recordable_stream is not None
        return self._recordable_stream.get_complete_stream()
