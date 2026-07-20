"""``SlicingStream`` — a bounded view over a region of another binary stream.

Used to present a member's byte range inside a container as a standalone stream, and (via
``fix_stream_start_position``) to give a mid-positioned stream a clean ``tell() == 0``
origin for codec libraries that assume it.

Two modes (same class — easy to miss):

1. **Single-consumer (no lock, historical default)** — ``read`` continues from wherever
   the underlying handle currently sits. Correct when only one view is live.
2. **SharedSource mode (``lock`` set)** — every ``read`` does
   ``seek(start + _pos); read(n)`` under the lock so interleaved views never clobber
   each other. Construction must not call unlocked ``tell``/``seek`` on that shared
   handle when ``start`` is already known — ``BufferedReader.tell`` is not thread-safe.
"""

from __future__ import annotations

import io
from contextlib import nullcontext
from typing import BinaryIO, Callable, ContextManager

from archivey.internal.streams.streamtools.base import ReadOnlyIOStream
from archivey.internal.streams.streamtools.binaryio import (
    is_seekable,
    read_exact,
    source_byte_size,
)


class SlicingStream(ReadOnlyIOStream):
    """A view over ``[start, start+length)`` of an underlying binary stream.

    Seekable underlying stream:
      - ``start`` is the absolute offset where the slice begins (default: the stream's
        current position).
      - ``length`` caps the slice (default: to the end of the underlying stream).
      - Seeking is relative to the start of the slice.

    Non-seekable underlying stream:
      - ``start`` must be ``None`` (the slice begins at the current position).
      - ``length`` caps how many bytes may be read; seeking is unsupported.

    Optional ``lock`` (SharedSource mode):
      - Every ``read`` does ``seek(start + _pos); read(n)`` under the lock so the
        seek+read pair is atomic across interleaved views.
      - Construction does not call ``tell``/``seek`` on the shared handle when
        ``start`` is given (and takes the lock if ``start`` must be read from
        ``tell``). Unlocked ``BufferedReader.tell`` under concurrency corrupts the
        buffer even when every later ``read`` is locked.
      - ``seek`` only updates this view's ``_pos`` (the next ``read`` re-seeks); without
        a lock, ``seek`` also repositions the underlying (single-consumer behaviour).
      - Internally split into ``_io_guard`` (the lock or a shared ``nullcontext``) and
        ``_seek_before_read`` so the re-seek policy is explicit and can later be
        engaged independently of locking.

    Optional ``check_open``: called at the start of I/O; raise to signal a closed source
    (SharedSource uses this so a closed factory poisons its views).

    It is a :class:`ReadOnlyIOStream`, not a :class:`DelegatingStream`: every operation is
    *transformed*, not forwarded — ``read`` clamps to the slice bounds, and ``seek``/``tell``
    are relative to the slice start, not the underlying offset. And by default it is a
    *non-owning view*: it does NOT close the underlying stream (the container owns it), whereas
    ``DelegatingStream.close`` closes its inner. So delegation would be both useless (almost
    everything is overridden) and unsafe (the close default). The opt-in ``own_source`` flag
    flips just the close behaviour for the case where the view is the sole owner of a private
    underlying stream (e.g. a per-member decoder); it never applies to a ``SharedSource`` view.

    It also does not expose ``name``: a slice view remaps the origin, so forwarding the
    underlying path would mislead libraries that reopen or stat by ``stream.name``.
    """

    def __init__(
        self,
        stream: BinaryIO,
        start: int | None = None,
        length: int | None = None,
        *,
        lock: ContextManager[object] | None = None,
        check_open: Callable[[], None] | None = None,
        own_source: bool = False,
    ) -> None:
        super().__init__()
        self._stream = stream
        # A view is non-owning by default (never closes the underlying — the container
        # owns it). ``own_source=True`` is the opt-in for the case where this view is the
        # sole owner of a private underlying stream (e.g. a per-member decoder opened just
        # for this slice) that should be closed together with the view.
        self._own_source = own_source
        self._seekable = is_seekable(stream)
        # Split lock into the I/O guard and the re-seek policy so the latter can later
        # be engaged without a lock (e.g. a single-consumer view that still re-seeks).
        self._io_guard: ContextManager[object] = (
            lock if lock is not None else nullcontext()
        )
        self._seek_before_read = lock is not None
        self._check_open_fn = check_open

        if self._seekable:
            # Re-seek mode (locked / SharedSource views): do not touch the shared handle
            # at construction — not even ``tell()``. ``io.BufferedReader.tell`` is not
            # thread-safe; concurrent unlocked tells while another view holds the lock
            # for seek+read corrupt the buffer and yield wrong bytes / CRC mismatches
            # (ZIP ``MemberStreams.CONCURRENT`` fan-out). Resolve a missing ``start``
            # under the guard; otherwise leave the handle alone until the first read.
            if self._seek_before_read:
                if start is None:
                    with self._io_guard:
                        start = stream.tell()
            else:
                initial_pos = stream.tell()
                if start is None:
                    start = initial_pos
                # Single-consumer: eager seek so the slice starts at ``start``.
                if initial_pos != start:
                    stream.seek(start)
        elif start is not None:
            raise ValueError("Cannot slice a non-seekable stream with a start position")

        self._start = start  # absolute start in the underlying stream (seekable only)
        self._length = length
        self._pos = 0  # position relative to the start of the slice

    def _check_open(self) -> None:
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        if self._check_open_fn is not None:
            self._check_open_fn()

    def _compute_bytes_to_read(self, n: int) -> int:
        if self._length is not None:
            remaining = self._length - self._pos
            if n < 0:
                return max(remaining, 0)
            return min(n, max(remaining, 0))
        return n

    def read(self, n: int = -1, /) -> bytes:
        self._check_open()
        # Bounded ``read()`` / ``read(-1)`` means drain the remaining slice. Short
        # underlying reads must be retried (via ``read_exact``) until the bound is
        # filled or EOF; a single ``read(remaining)`` may legally return a partial
        # chunk. Unbounded slices still pass ``read(-1)`` through unchanged.
        drain_bounded = n < 0 and self._length is not None
        n = self._compute_bytes_to_read(n)
        if n == 0:
            return b""
        with self._io_guard:
            self._check_open()
            # Re-seek mode: reposition to this view's absolute offset so interleaved
            # views never clobber each other. Otherwise read from wherever the handle
            # currently sits (single-consumer contract).
            if self._seek_before_read:
                assert self._start is not None  # re-seek views are always seekable
                self._stream.seek(self._start + self._pos)
            data = (
                read_exact(self._stream, n) if drain_bounded else self._stream.read(n)
            )
            self._pos += len(data)
            return data

    def tell(self, /) -> int:
        self._check_open()
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        self._check_open()
        if not self._seekable:
            raise io.UnsupportedOperation("seek on non-seekable stream")

        assert self._start is not None  # always set for seekable streams
        start_abs = self._start

        if whence == io.SEEK_SET:
            new_relative = offset
        elif whence == io.SEEK_CUR:
            new_relative = self._pos + offset
        elif whence == io.SEEK_END:
            if self._length is None:
                # No declared length: the slice ends where the underlying stream does,
                # so probe that end on demand.
                with self._io_guard:
                    self._check_open()
                    end_relative = self._stream.seek(0, io.SEEK_END) - start_abs
            else:
                end_relative = self._length
            new_relative = end_relative + offset
        else:
            raise ValueError(f"Invalid whence: {whence}")

        if new_relative < 0:
            # Match BytesIO: a relative seek (SEEK_CUR/SEEK_END) that underflows clamps
            # to the origin; only an explicitly negative SEEK_SET raises. Callers probing
            # backwards from the end (e.g. ZipFile's ``seek(-22, SEEK_END)`` EOCD probe
            # on a short source) rely on the clamp rather than a raw ``ValueError``.
            if whence == io.SEEK_SET:
                raise ValueError("Negative seek position")
            new_relative = 0

        # Seeking past a defined end is allowed (reads clamp to empty), matching BytesIO.
        if not self._seek_before_read:
            # Single-consumer: keep the underlying handle in sync with the view.
            self._stream.seek(start_abs + new_relative)
        # Re-seek mode: only update _pos — the next read re-seeks under the guard.
        self._pos = new_relative
        return self._pos

    def seekable(self) -> bool:
        return self._seekable

    def close(self) -> None:
        # Non-owning by default: mark this view closed only. With ``own_source`` the view
        # owns a private underlying stream and closes it too.
        if not self.closed:
            if self._own_source:
                self._stream.close()
            super().close()

    @property
    def size(self) -> int | None:
        """Total slice length when cheaply knowable (the fsspec-style ``size`` convention).

        A declared ``length`` answers directly; an open-ended slice derives it from the
        underlying stream's cheap size (``source_byte_size``), and reports ``None`` when
        that is unknowable — never by an expensive end-seek.
        """
        if self._length is not None:
            return self._length
        if self._start is None:
            return None  # non-seekable underlying stream: length unknowable cheaply
        underlying = source_byte_size(self._stream)
        if underlying is None:
            return None
        return max(underlying - self._start, 0)


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
