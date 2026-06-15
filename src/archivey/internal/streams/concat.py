"""Stream that concatenates multiple streams sequentially."""

import io
from typing import BinaryIO

from archivey.types import ReadableStreamLikeOrSimilar


class ConcatenationStream(io.RawIOBase, BinaryIO):
    """Concatenate multiple streams sequentially."""

    _streams: list[ReadableStreamLikeOrSimilar]
    _index: int

    def __init__(self, streams: list[ReadableStreamLikeOrSimilar]):
        super().__init__()

        # Flatten multiple concatenation streams to avoid extra overhead.
        flattened_streams: list[ReadableStreamLikeOrSimilar] = []

        for stream in streams:
            if isinstance(stream, ConcatenationStream):
                flattened_streams.extend(stream._streams[stream._index :])
            else:
                flattened_streams.append(stream)

        self._streams = flattened_streams
        self._index = 0

    # Basic IO methods -------------------------------------------------
    def read(self, n: int = -1) -> bytes:
        if self.closed:
            raise ValueError("I/O operation on closed file.")

        if n == -1:
            return b"".join(stream.read() for stream in self._streams)

        while self._index < len(self._streams):
            data = self._streams[self._index].read(n)
            if data:
                return data
            self._index += 1

        # All streams are exhausted.
        return b""

    def readinto(self, b: bytearray | memoryview) -> int:  # type: ignore[override]
        data = self.read(len(b))
        n = len(data)
        b[:n] = data
        return n

    # Properties -------------------------------------------------------
    def readable(self) -> bool:  # pragma: no cover - trivial
        return True

    def writable(self) -> bool:  # pragma: no cover - trivial
        return False

    def seekable(self) -> bool:  # pragma: no cover - trivial
        return False

    def fileno(self) -> int:  # pragma: no cover - simple
        raise OSError("fileno")

    # Control methods --------------------------------------------------
    def close(self) -> None:  # pragma: no cover - simple delegation
        super().close()
