"""``PeekableStream`` — buffer a non-seekable source's prefix so detection never
consumes it.

Format detection needs to inspect the leading bytes of a source, but a non-seekable
stream (a socket or pipe) cannot be rewound after that inspection. The opener wraps
such a source in a :class:`PeekableStream` **once**, runs detection through
:meth:`PeekableStream.peek`, and hands the *same* wrapper to the backend; reads then
replay the buffered prefix before falling through to the underlying stream, so no bytes
are dropped (see the ``format-detection`` capability).

This is the fresh v2 replacement for DEV's ``RecordableStream`` /
``RewindableStreamWrapper``.
"""

from __future__ import annotations

from typing import BinaryIO

from archivey.internal.streams.streamtools import ReadOnlyIOStream

# Default amount buffered for detection (matches ``format-detection``'s DETECTION_LIMIT).
# The buffer grows on demand up to whatever ``peek(n)`` asks for — e.g. 32 774 bytes when
# the ISO probe is triggered — so this is only the typical case, not a hard cap.
DETECTION_LIMIT = 4096


class PeekableStream(ReadOnlyIOStream):
    """A read-only ``BinaryIO`` over a non-seekable source with a peekable prefix.

    ``peek(n)`` returns the first ``n`` unconsumed bytes without advancing the read
    position, reading ahead from the underlying stream into an internal buffer as needed.
    ``read`` drains that buffer first, then passes through to the underlying stream.

    The wrapper never closes the underlying stream: like the rest of the stream layer it
    adapts a source the caller owns, so closing this wrapper must not take the caller's
    stream down with it.
    """

    def __init__(self, underlying: BinaryIO) -> None:
        super().__init__()
        self._underlying = underlying
        # Bytes read ahead from the underlying stream but not yet consumed by read().
        self._buffer = bytearray()
        # Total bytes consumed via read() (the logical position of this stream).
        self._pos = 0

    def _fill_to(self, n: int) -> None:
        """Read ahead until the buffer holds ``n`` bytes or the underlying stream ends."""
        while len(self._buffer) < n:
            chunk = self._underlying.read(n - len(self._buffer))
            if not chunk:
                break
            self._buffer.extend(chunk)

    def peek(self, n: int) -> bytes:
        """Return up to the first ``n`` unconsumed bytes without consuming them.

        Fewer than ``n`` bytes are returned only when the underlying stream ends first.
        """
        if n < 0:
            raise ValueError("peek size must be non-negative")
        self._fill_to(n)
        return bytes(self._buffer[:n])

    def read(self, size: int | None = -1, /) -> bytes:
        if size is None or size < 0:
            data = bytes(self._buffer) + (self._underlying.read() or b"")
            self._buffer.clear()
            self._pos += len(data)
            return data
        self._fill_to(size)
        data = bytes(self._buffer[:size])
        del self._buffer[:size]
        self._pos += len(data)
        return data

    def seekable(self) -> bool:
        # A peekable stream is forward-only by construction (it wraps a non-seekable
        # source); the peeked prefix is replayed by read(), not by seek().
        return False

    def tell(self, /) -> int:
        return self._pos

    @property
    def name(self) -> str | None:
        name = getattr(self._underlying, "name", None)
        return name if isinstance(name, str) else None

    def close(self) -> None:
        # Do NOT close the underlying stream — the caller owns it.
        super().close()

    def __repr__(self) -> str:
        return f"PeekableStream({self._underlying!r})"
