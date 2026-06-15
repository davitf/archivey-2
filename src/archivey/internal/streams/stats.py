"""I/O statistics tracking stream wrapper."""

import io
from dataclasses import dataclass, field
from typing import IO, Any, BinaryIO


@dataclass
class IOStats:
    """Simple container for I/O statistics."""

    bytes_read: int = 0
    seek_calls: int = 0
    read_ranges: list[list[int]] = field(default_factory=lambda: [[0, 0]])


class StatsIO(io.RawIOBase, BinaryIO):
    """
    An I/O stream wrapper that tracks statistics about read and seek operations
    performed on an underlying stream.

    This can be useful for debugging, performance analysis, or understanding
    access patterns.
    """

    def __init__(self, inner: IO[bytes], stats: IOStats) -> None:
        super().__init__()
        self._inner = inner
        self.stats = stats

    # Basic IO methods -------------------------------------------------
    def read(self, n: int = -1) -> bytes:
        data = self._inner.read(n)
        self.stats.bytes_read += len(data)
        self.stats.read_ranges[-1][1] += len(data)
        return data

    def readinto(self, b: bytearray | memoryview) -> int:  # type: ignore[override]
        try:
            n: int = self._inner.readinto(b)  # type: ignore[attr-defined]
            self.stats.bytes_read += n
            self.stats.read_ranges[-1][1] += n
            return n
        except NotImplementedError:
            # Some streams don't support readinto, so we fall back to read()
            data = self.read(len(b))
            b[: len(data)] = data
            self.stats.bytes_read += len(data)
            self.stats.read_ranges[-1][1] += len(data)
            return len(data)

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        newpos = self._inner.seek(offset, whence)
        if offset != 0 or whence != 1:
            # Called by IOBase.tell(), doesn't actually move the stream. Ignore these seeks.
            self.stats.seek_calls += 1
            self.stats.read_ranges.append([newpos, 0])

        return newpos

    def readable(self) -> bool:  # pragma: no cover - trivial
        return self._inner.readable()

    def writable(self) -> bool:  # pragma: no cover - trivial
        return self._inner.writable()

    def seekable(self) -> bool:  # pragma: no cover - trivial
        return self._inner.seekable()

    def write(self, b: Any) -> int:  # pragma: no cover - simple delegation
        return self._inner.write(b)

    def close(self) -> None:  # pragma: no cover - simple delegation
        self._inner.close()
        super().close()

    # Delegate unknown attributes --------------------------------------
    def __getattr__(self, item: str) -> Any:  # pragma: no cover - simple
        return getattr(self._inner, item)
