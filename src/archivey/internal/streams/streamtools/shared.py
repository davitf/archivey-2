"""``SharedSource`` — concurrent-safe byte-range views over one underlying source.

A source (an OS file handle or a seekable ``BinaryIO``) has exactly one file position.
Two consumers that each ``seek``+``read`` the same handle will clobber each other's
offset — even on a single thread. :class:`SharedSource` mints independent, seekable,
**non-owning** views over ``[start, start+length)``; each view keeps its own ``_pos``,
and every read re-seeks the underlying to that absolute position under a shared lock so
the seek+read pair is atomic.

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

import io
import os
import threading
from pathlib import Path
from typing import BinaryIO

from archivey.internal.streams.streamtools.base import ReadOnlyIOStream
from archivey.internal.streams.streamtools.binaryio import (
    is_filename,
    is_seekable,
    source_byte_size,
)


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
        known, a view whose bounds fall outside it raises ``ValueError`` at construction
        (no silent short/garbage reads later).

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
            if start > size:
                raise ValueError(
                    f"view start {start} is past the end of the source (size {size})"
                )
            if length is not None and start + length > size:
                raise ValueError(
                    f"view [{start}, {start + length}) exceeds source size {size}"
                )
            if length is None:
                length = size - start

        # independent_handles is dormant: always share ``_handle``. When engaged for a
        # path source, mint ``open(self._path, "rb")`` here instead and adjust close
        # semantics so the per-view FD is owned by the view.
        _ = self._independent_handles  # documented seam; intentionally unused for now
        return _SharedSourceView(self, start=start, length=length)

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


class _SharedSourceView(ReadOnlyIOStream):
    """Non-owning seekable view: re-seeks the shared handle under the source lock on read.

    Composes the same bound/tell logic as :class:`SlicingStream`, but unlike that class
    every ``read`` repositions the underlying to ``start + _pos`` under the lock first —
    so interleaved views never clobber each other. Closing the view does **not** close
    the :class:`SharedSource` or its handle.
    """

    def __init__(self, source: SharedSource, *, start: int, length: int | None) -> None:
        super().__init__()
        self._source = source
        self._start = start
        self._length = length
        self._pos = 0

    def _check_open(self) -> None:
        if self.closed or self._source.closed:
            raise ValueError("I/O operation on closed file.")

    def _compute_bytes_to_read(self, n: int) -> int:
        if self._length is not None:
            remaining = self._length - self._pos
            if n < 0:
                return max(remaining, 0)
            return min(n, max(remaining, 0))
        return n

    def read(self, n: int = -1, /) -> bytes:
        self._check_open()
        n = self._compute_bytes_to_read(n)
        if n == 0:
            return b""
        with self._source._lock:
            self._check_open()
            self._source._handle.seek(self._start + self._pos)
            data = self._source._handle.read(n)
            self._pos += len(data)
            return data

    def tell(self, /) -> int:
        self._check_open()
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        self._check_open()
        if whence == io.SEEK_SET:
            new_relative = offset
        elif whence == io.SEEK_CUR:
            new_relative = self._pos + offset
        elif whence == io.SEEK_END:
            if self._length is None:
                with self._source._lock:
                    self._check_open()
                    end_relative = (
                        self._source._handle.seek(0, io.SEEK_END) - self._start
                    )
            else:
                end_relative = self._length
            new_relative = end_relative + offset
        else:
            raise ValueError(f"Invalid whence: {whence}")

        if new_relative < 0:
            # Match BytesIO exactly: a relative seek (SEEK_CUR/SEEK_END) that underflows
            # clamps to the origin; only an explicitly negative SEEK_SET raises. Callers
            # probing backwards from the end (e.g. ZipFile's ``seek(-22, SEEK_END)`` EOCD
            # probe on a short source) rely on the clamp rather than a raw ``ValueError``.
            if whence == io.SEEK_SET:
                raise ValueError("Negative seek position")
            new_relative = 0

        # Seeking past a defined end is allowed (reads clamp to empty), matching BytesIO /
        # SlicingStream. Only update the view's _pos — do not touch the shared handle here;
        # the next read re-seeks under the lock.
        self._pos = new_relative
        return self._pos

    def seekable(self) -> bool:
        return True

    def close(self) -> None:
        # Non-owning: mark this view closed only; the SharedSource owns the handle.
        if not self.closed:
            super().close()

    # No ``name`` property: like SlicingStream, a view remaps the origin, and no current
    # construction hands views a path identity (the single-file backend passes path
    # sources to codecs as paths, keeping path-only features like the rapidgzip ISIZE
    # backstop on independent handles). If the dormant path-backed mode is engaged later,
    # revisit how path-only codec features discover the path.

    @property
    def size(self) -> int | None:
        if self._length is not None:
            return self._length
        underlying = self._source.size
        if underlying is None:
            return None
        return max(underlying - self._start, 0)
