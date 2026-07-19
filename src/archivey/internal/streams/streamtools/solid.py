"""Demultiplex one forward-only decoded stream into consecutive member sub-streams.

A *solid block* — a 7z folder, or a RAR ``unrar p`` pipe — decodes to a single
forward-only (often non-seekable) byte stream whose members occupy consecutive,
known-size ranges. :class:`SolidBlockReader` owns that stream and hands out one member
sub-stream at a time, skipping forward **lazily**: the gap before a member is consumed
only when the *next* member is opened, so closing a never-advanced member costs nothing.

Not the same as :class:`~archivey.internal.streams.streamtools.shared.SharedSource`
(independent seekable views) or :class:`~archivey.internal.streams.streamtools.locked.LockedStream`
(lock around seek+read on one handle). This class is deliberately forward-only.

``open_member(..., lazy=True)`` defers open/skip until the first read — so an
iterator that yields then closes an unselected member pays nothing.

Like the rest of ``streamtools``, truncated blocks surface as plain :class:`EOFError`
for the caller to translate.
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

    When constructed with ``pending=True`` (``open_member(..., lazy=True)``), the
    skip-to-``offset`` runs on first read rather than at construction; closing without
    reading never touches the block.
    """

    def __init__(
        self,
        reader: SolidBlockReader,
        offset: int,
        size: int,
        *,
        pending: bool = False,
    ) -> None:
        super().__init__()
        self._reader = reader
        self._offset = offset
        self._size = size
        self._remaining = size
        self._pending = pending

    def _ensure_positioned(self) -> None:
        if not self._pending:
            return
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        reader = self._reader
        if reader._closed:
            raise ValueError("SolidBlockReader is closed")
        if self._offset < reader._pos:
            raise ValueError(
                f"solid members must be opened in order: offset {self._offset} < "
                f"position {reader._pos}"
            )
        # Finalize any prior active member and jump the gap (same as eager open_member).
        reader._current = None
        skip_forward(reader._block, self._offset - reader._pos)
        reader._pos = self._offset
        reader._current = self
        self._pending = False

    def read(self, n: int = -1, /) -> bytes:
        self._ensure_positioned()
        if self._remaining <= 0:
            return b""
        if n < 0 or n > self._remaining:
            n = self._remaining
        data = self._reader._consume(n)
        self._remaining -= len(data)
        return data

    def tell(self) -> int:
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        if self._pending:
            return 0
        return self._size - self._remaining

    def close(self) -> None:
        if self.closed:
            return
        # Unopened pending slices must not touch the block.
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
    handle without reading never skip-decodes. Eager and lazy both return the same
    :class:`_MemberSlice` type — no extra wrapper layer.
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
            return _MemberSlice(self, offset, size, pending=True)
        # Finalize the previous member and jump the gap in one forward skip. This is where
        # a prior member's unread tail is actually consumed (lazy drain).
        self._current = None
        skip_forward(self._block, offset - self._pos)
        self._pos = offset
        slice_ = _MemberSlice(self, offset, size, pending=False)
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
