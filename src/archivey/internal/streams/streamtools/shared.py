"""``SharedSource`` — concurrent-safe byte-range views over one underlying source.

A source (an OS file handle or a seekable ``BinaryIO``) has exactly one file position.
Two consumers that each ``seek``+``read`` the same handle will clobber each other's
offset — even on a single thread. :class:`SharedSource` mints independent, seekable,
**non-owning** views over ``[start, start+length)``; each view keeps its own ``_pos``,
and every read re-seeks the underlying to that absolute position under a shared lock so
the seek+read pair is atomic.

Views are :class:`~archivey.internal.streams.streamtools.slice.SlicingStream` instances
with the source lock engaged (design §H: compose, don't replace) — the same bound/tell
logic, plus lock+reseek on every read.

This is the streamtools analogue of stdlib ``zipfile._SharedFile``. It is deliberately
archivey-dependency-free: it raises stdlib-shaped errors (``ValueError`` / ``OSError`` /
``io.UnsupportedOperation``), never ``archivey.exceptions``.

**Path-source independent handles (dormant).** For a path source, ``view()`` *can* mint a
fresh ``open(path, 'rb')`` handle per view for true parallel I/O (see
``independent_handles``). That seam is **default off** for now: every view shares one
handle + lock. Engaging live per-view handles belongs with parallel extraction.

**Views are unbuffered.** Every ``read`` is one locked seek+read on the shared handle —
cheap (an in-memory offset move on a regular file / ``BytesIO``), and the current
consumers (codec streams, which buffer internally) already read in large chunks. A
consumer that issues many tiny reads should wrap its view in ``io.BufferedReader``
itself; buffering inside the primitive would double-copy for everyone else.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import BinaryIO

from archivey.internal.streams.streamtools.binaryio import (
    is_filename,
    is_seekable,
    source_byte_size,
)
from archivey.internal.streams.streamtools.slice import SlicingStream


class SharedSource:
    """Factory for locked, per-view-position slices over one seekable source.

    Construct from a :class:`~pathlib.Path` (opens and owns the handle) or an already-open
    seekable ``BinaryIO`` (does **not** take ownership — the caller closes it).
    """

    def __init__(
        self,
        source: Path | BinaryIO,
        *,
        independent_handles: bool = False,
    ) -> None:
        # Seam for true parallel I/O on path sources (fresh FD per view). Dormant:
        # ignored for now; every view shares ``_handle`` + ``_lock``. See module docstring.
        self._independent_handles = independent_handles

        self._lock = threading.Lock()
        self._closed = False
        self._path: Path | None = None
        self._owns_handle = False

        if isinstance(source, Path) or is_filename(source):
            # ``is_filename`` admits bytes; Path wants str/PathLike[str], so fsdecode.
            path = source if isinstance(source, Path) else Path(os.fsdecode(source))
            self._path = path
            self._handle: BinaryIO = open(path, "rb")
            self._owns_handle = True
            self._size: int | None = source_byte_size(path)
        else:
            if not is_seekable(source):
                raise ValueError("SharedSource requires a seekable BinaryIO source")
            self._handle = source
            self._size = source_byte_size(source)

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def size(self) -> int | None:
        """Cheap total byte size of the source, when knowable."""
        return self._size

    def view(self, start: int, length: int | None = None) -> BinaryIO:
        """Mint a non-owning seekable view over ``[start, start+length)``.

        ``length is None`` means "to the end of the source". When the source size is
        known, a view that extends past EOF is **clamped** to the available bytes (like a
        real stream / :class:`SlicingStream`) — so a backend opening a member from a
        truncated archive still gets a readable short view instead of failing at
        construction. Negative ``start``/``length`` remain hard errors.

        When ``independent_handles`` is eventually engaged for a path source, this is the
        entry point that would open a fresh ``open(path, 'rb')`` per view; today every
        view shares the single locked handle.
        """
        self._check_open()
        if start < 0:
            raise ValueError(f"view start must be non-negative, got {start}")
        if length is not None and length < 0:
            raise ValueError(f"view length must be non-negative, got {length}")

        size = self._size
        if size is not None:
            if start >= size:
                # Past EOF: empty view (reads return b""), matching a real stream seek
                # past the end. Keep ``start`` so tell/seek stay well-defined.
                length = 0
            elif length is None:
                length = size - start
            else:
                length = min(length, size - start)

        # independent_handles is dormant: always share ``_handle``. When engaged for a
        # path source, mint ``open(self._path, "rb")`` here instead and adjust close
        # semantics so the per-view FD is owned by the view.
        _ = self._independent_handles  # documented seam; intentionally unused for now
        return SlicingStream(
            self._handle,
            start=start,
            length=length,
            lock=self._lock,
            check_open=self._check_open,
        )

    def close(self) -> None:
        """Mark closed and close an owned path handle; never closes a caller-owned stream."""
        if self._closed:
            return
        self._closed = True
        if self._owns_handle:
            self._handle.close()

    def __enter__(self) -> SharedSource:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _check_open(self) -> None:
        if self._closed:
            raise ValueError("I/O operation on closed file.")
