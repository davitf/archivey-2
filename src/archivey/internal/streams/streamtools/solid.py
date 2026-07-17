"""Demultiplex one forward-only decoded stream into consecutive member sub-streams.

A *solid block* — a 7z folder, or a RAR ``unrar p`` pipe — decodes to a single
forward-only (often non-seekable) byte stream whose members occupy consecutive,
known-size ranges. :class:`SolidBlockReader` owns that stream and hands out one member
sub-stream at a time, skipping forward **lazily**: the gap before a member (a preceding
member's unread tail, or an inter-member gap) is consumed only when the *next* member is
opened, so closing a member the caller never advances past costs nothing. Closing the
reader closes the block without draining.

``open_member(..., lazy=True)`` defers that open (and its skip-decode) until the first
read on the returned handle — so an iterator that yields then closes an unselected
member pays nothing. This is the sequential/iteration primitive. Random access into a
solid block is served differently (a seekable slice over a seekable decode), so this
class is deliberately forward-only and does not seek.

Like the rest of ``streamtools`` this module knows nothing about archivey's error
hierarchy: a truncated block surfaces as a plain :class:`EOFError` for the caller to
translate into a format-specific error.
"""

from __future__ import annotations

from typing import BinaryIO

from archivey.internal.streams.streamtools.base import ReadOnlyIOStream

_SKIP_CHUNK = 1 << 20  # 1 MiB


def skip_forward(stream: BinaryIO, count: int) -> None:
    """Read and discard exactly ``count`` bytes from a forward-only ``stream``.

    Raises :class:`EOFError` if the stream ends before ``count`` bytes are consumed.
    """
    while count > 0:
        chunk = stream.read(min(count, _SKIP_CHUNK))
        if not chunk:
            raise EOFError("stream ended before the requested position")
        count -= len(chunk)


class _MemberSlice(ReadOnlyIOStream):
    """One member's forward-only view over its owning reader's block stream.

    Non-owning: closing a member slice never closes the shared block — the
    :class:`SolidBlockReader` owns that. Reads are bounded to the member's declared size
    and flow through the reader so it always knows the block's position.
    """

    def __init__(self, reader: SolidBlockReader, size: int) -> None:
        super().__init__()
        self._reader = reader
        self._size = size
        self._remaining = size

    def read(self, n: int = -1, /) -> bytes:
        if self._remaining <= 0:
            return b""
        if n < 0 or n > self._remaining:
            n = self._remaining
        data = self._reader._consume(n)
        self._remaining -= len(data)
        return data

    def tell(self) -> int:
        return self._size - self._remaining


class _LazyMember(ReadOnlyIOStream):
    """Deferred ``open_member``: skip-decode runs on first read, not at construction.

    Closing without reading never touches the block. Useful when a sequential pass
    yields every member but only some are selected — the unselected handles close
    free.
    """

    def __init__(self, reader: SolidBlockReader, offset: int, size: int) -> None:
        super().__init__()
        self._reader = reader
        self._offset = offset
        self._size = size
        self._inner: BinaryIO | None = None

    def _ensure(self) -> BinaryIO:
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        if self._inner is None:
            self._inner = self._reader.open_member(self._offset, self._size, lazy=False)
        return self._inner

    def read(self, n: int = -1, /) -> bytes:
        return self._ensure().read(n)

    def tell(self) -> int:
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        if self._inner is None:
            return 0
        return self._inner.tell()

    def close(self) -> None:
        if self.closed:
            return
        if self._inner is not None:
            self._inner.close()
            self._inner = None
        super().close()


class SolidBlockReader:
    """Vend consecutive member sub-streams over one forward-only decoded block.

    ``open_member(offset, size)`` returns a forward-only stream for the member occupying
    ``[offset, offset + size)`` of the decoded block. Members must be opened in
    non-decreasing ``offset`` order; the reader skips forward from its current position to
    ``offset`` lazily, at open time, so a partially-read (or unread) member costs nothing
    until the next one is requested. Only one member is active at a time.

    Pass ``lazy=True`` to defer that open until the first read on the returned handle
    (claim-time still rejects ``offset`` behind the current position). Closing a lazy
    handle without reading never skip-decodes.
    """

    def __init__(self, block: BinaryIO, *, close_block: bool = True) -> None:
        self._block = block
        self._close_block = close_block
        self._pos = 0  # bytes consumed from the block so far
        self._current: _MemberSlice | None = None
        self._closed = False

    def open_member(self, offset: int, size: int, *, lazy: bool = False) -> BinaryIO:
        if self._closed:
            raise ValueError("SolidBlockReader is closed")
        if offset < self._pos:
            raise ValueError(
                f"solid members must be opened in order: offset {offset} < position "
                f"{self._pos}"
            )
        if lazy:
            return _LazyMember(self, offset, size)
        # Finalize the previous member and jump the gap in one forward skip. This is where
        # a prior member's unread tail is actually consumed (lazy drain).
        self._current = None
        skip_forward(self._block, offset - self._pos)
        self._pos = offset
        slice_ = _MemberSlice(self, size)
        self._current = slice_
        return slice_

    def _consume(self, n: int) -> bytes:
        data = self._block.read(n)
        self._pos += len(data)
        return data

    def close(self) -> None:
        # No draining: whatever is left in the block is discarded with it.
        if self._closed:
            return
        self._closed = True
        self._current = None
        if self._close_block:
            self._block.close()

    def __enter__(self) -> SolidBlockReader:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
