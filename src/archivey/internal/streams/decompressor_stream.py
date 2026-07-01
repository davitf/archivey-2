"""Seekable decompressor-stream base class (codec-agnostic).

The base supports optional seek-point-based random access: subclasses register known
positions as they decode (``add_seek_points``) and/or build a full index once on demand
(``_build_index``). The concrete backends build on this: the zlib/deflate and Brotli
streams in ``decompress.py`` and the segmented XZ / lzip streams in ``xz.py`` / ``lzip.py``.

This module holds no codec itself — only the shared scaffolding (``DecompressorStream``,
``SegmentedDecompressorStream``, ``SeekPoint``).
"""

from __future__ import annotations

import abc
import bisect
import io
import os
from dataclasses import dataclass, field
from typing import (
    Any,
    BinaryIO,
    Callable,
    Generic,
    Protocol,
    TypeVar,
    cast,
)

from archivey.exceptions import CorruptionError, TruncatedError
from archivey.internal.logs import streams as logger
from archivey.internal.streams.streamtools import ReadOnlyIOStream, ensure_bufferedio


@dataclass(order=True)
class SeekPoint:
    """A point from which decompression can resume.

    Ordered by ``decompressed_offset`` only, so ``bisect`` over a ``list[SeekPoint]``
    works without a ``key=`` argument.
    """

    decompressed_offset: int
    compressed_offset: int = field(compare=False)
    state: Any = field(default=None, compare=False)


DecompressorT = TypeVar("DecompressorT")


class _SegmentDecompressor(Protocol):
    """Interface shared by the lzip/XZ state machines and the XZ block chain."""

    def feed(self, data: bytes) -> tuple[bytes, list[tuple[int, int]]]: ...
    def flush(self) -> tuple[bytes, list[tuple[int, int]]]: ...
    def is_finished(self) -> bool: ...


_SDT = TypeVar("_SDT", bound=_SegmentDecompressor)


class DecompressorStream(ReadOnlyIOStream, Generic[DecompressorT]):
    """Seekable decompressor stream with optional seek-point-based random access.

    ``readable``/``writable``/``write``/``readinto`` come from :class:`ReadOnlyIOStream`
    (``readinto`` is built on this class's ``read``); subclasses provide the decode primitives.
    """

    def __init__(self, path: str | os.PathLike[str] | BinaryIO) -> None:
        super().__init__()
        if isinstance(path, (str, os.PathLike)):
            self._inner: BinaryIO = open(os.fspath(path), "rb")
            self._should_close = True
        else:
            self._inner = cast("BinaryIO", ensure_bufferedio(path))
            self._should_close = False
        self._seek_points: list[SeekPoint] = [SeekPoint(0, 0)]
        self._index_built = False
        self._index_build_attempted = False
        self._decompressor: DecompressorT = self._create_decompressor(self._seek_points[0])
        self._buffer = bytearray()
        self._eof = False
        self._pos = 0
        self._size: int | None = None

    @abc.abstractmethod
    def _create_decompressor(self, point: SeekPoint) -> DecompressorT: ...

    @abc.abstractmethod
    def _decompress_chunk(self, chunk: bytes) -> bytes: ...

    @abc.abstractmethod
    def _flush_decompressor(self) -> bytes:
        """Flush pending data once the compressed input is exhausted.

        Most decompressors decode eagerly and return ``b""`` here; zlib is the exception
        (its ``flush()`` emits the last decompressed bytes). The decompressor must not be
        used after this call.
        """
        ...

    @abc.abstractmethod
    def _is_decompressor_finished(self) -> bool: ...

    def seekable(self) -> bool:
        return self._inner.seekable()

    def add_seek_points(self, points: list[SeekPoint]) -> None:
        """Merge ``points`` into the sorted index, skipping duplicates.

        Pass points in ascending ``decompressed_offset`` order; the common in-order case
        is an O(1) append, out-of-order insertions fall back to bisect.
        """
        for point in points:
            if point < self._seek_points[-1]:
                i = bisect.bisect_left(self._seek_points, point)
                if i < len(self._seek_points) and self._seek_points[i] == point:
                    continue
                self._seek_points.insert(i, point)
            elif self._seek_points[-1] == point:
                continue
            else:
                self._seek_points.append(point)

    def _find_best_seek_point(self, pos: int) -> SeekPoint:
        """The last seek point with ``decompressed_offset <= pos``."""
        i = bisect.bisect_right(self._seek_points, SeekPoint(pos, 0)) - 1
        return self._seek_points[i]

    def _reset_to_seek_point(self, point: SeekPoint) -> None:
        self._inner.seek(point.compressed_offset)
        self._decompressor = self._create_decompressor(point)
        self._buffer.clear()
        self._eof = False
        self._pos = point.decompressed_offset

    def _build_index(self, last_known: SeekPoint) -> tuple[list[SeekPoint], int | None]:
        """One-shot full index build. Default: no-op.

        Subclasses that support random access override this to return new seek points and
        the total decompressed size (or ``None`` if unknown). Called at most once.
        """
        return [], None

    def _read_decompressed_chunk(self) -> bytes:
        chunk = self._inner.read(65536)
        if not chunk:
            self._eof = True
            leftover = self._flush_decompressor()
            if not self._is_decompressor_finished():
                raise TruncatedError("File is truncated")
            self._size = self._pos + len(self._buffer) + len(leftover)
            self._index_built = True  # a forward scan to EOF is a complete index
            return leftover
        return self._decompress_chunk(chunk)

    def readall(self) -> bytes:
        while not self._eof:
            self._buffer.extend(self._read_decompressed_chunk())
        data = bytes(self._buffer)
        self._buffer.clear()
        if self._size is None or self._pos <= self._size:
            self._pos += len(data)
            self._size = self._pos
        return data

    def read(self, n: int = -1, /) -> bytes:
        if n == 0:
            return b""
        if n is None or n < 0:
            return self.readall()
        if len(self._buffer) < n and not self._eof:
            self._buffer.extend(self._read_decompressed_chunk())
        data = bytes(self._buffer[:n])
        del self._buffer[:n]
        self._pos += len(data)
        return data

    def close(self) -> None:
        if self._should_close:
            self._inner.close()
        super().close()

    def _ensure_index_built(self) -> None:
        if self._index_built or self._index_build_attempted:
            return
        inner_pos = self._inner.tell()
        new_points, new_size = self._build_index(self._seek_points[-1])
        self._index_build_attempted = True
        if new_points or new_size is not None:
            self._index_built = True
        if new_points:
            self.add_seek_points(new_points)
        if new_size is not None:
            self._size = new_size
        # _build_index may have seeked _inner (e.g. lzip's backward trailer scan);
        # restore it so the decompressor's expected read position is still valid.
        if self._inner.tell() != inner_pos:
            self._inner.seek(inner_pos)

    def try_get_size(self) -> int | None:
        """The total decompressed size if cheaply available (via the index), else ``None``."""
        if self._size is not None:
            return self._size
        if not self._inner.seekable():
            return None
        self._ensure_index_built()
        return self._size

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        if not self._inner.seekable():
            raise io.UnsupportedOperation("seek")

        if whence == io.SEEK_SET:
            new_pos = offset
        elif whence == io.SEEK_CUR:
            new_pos = self._pos + offset
        elif whence == io.SEEK_END:
            new_pos = -1  # resolved below once _size is known
        else:
            raise ValueError(f"Invalid whence: {whence}")

        if whence == io.SEEK_END or (
            new_pos > self._pos + len(self._buffer)
            and new_pos > self._seek_points[-1].decompressed_offset
        ):
            self._ensure_index_built()

        if whence == io.SEEK_END:
            if self._size is None:
                # Building the index didn't reveal the size; scan to EOF to find it
                # without buffering all remaining data in RAM.
                self._pos += len(self._buffer)
                self._buffer.clear()
                while not self._eof:
                    data = self._read_decompressed_chunk()
                    self._pos += len(data)
                assert self._size is not None
            new_pos = self._size + offset

        if new_pos < 0:
            raise ValueError(f"Invalid offset: {offset}")

        if self._size is not None and new_pos >= self._size:
            self._buffer.clear()
            self._eof = True
            self._pos = new_pos
            return self._pos

        if new_pos == self._pos:
            return self._pos

        if new_pos < self._pos:
            self._reset_to_seek_point(self._find_best_seek_point(new_pos))
        elif new_pos <= self._pos + len(self._buffer):
            del self._buffer[: new_pos - self._pos]
            self._pos = new_pos
            return self._pos
        else:
            best = self._find_best_seek_point(new_pos)
            if best.decompressed_offset > self._pos:
                self._reset_to_seek_point(best)
            else:
                self._pos += len(self._buffer)
                self._buffer.clear()

        assert not self._buffer
        if self._pos == new_pos:
            return self._pos

        while not self._eof:
            decompressed = self._read_decompressed_chunk()
            if self._pos + len(decompressed) >= new_pos:
                self._buffer.extend(decompressed[new_pos - self._pos :])
                self._pos = new_pos
                return self._pos
            self._pos += len(decompressed)

        self._pos = new_pos
        return self._pos

    def tell(self, /) -> int:
        return self._pos


class SegmentedDecompressorStream(DecompressorStream[_SDT]):
    """Base for multi-segment compressed formats (lzip, XZ).

    Tracks compressed/decompressed cursors and delegates feed/flush to a state-machine
    decompressor; provides the shared backward-scan index skeleton.
    """

    def __init__(self, path: str | os.PathLike[str] | BinaryIO) -> None:
        # Pre-declare cursors: super().__init__ calls _create_decompressor, which reads them.
        self._comp_cursor = 0
        self._decomp_cursor = 0
        super().__init__(path)

    @abc.abstractmethod
    def _make_decompressor(self, point: SeekPoint) -> _SDT: ...

    @abc.abstractmethod
    def _on_completed_segments(self, units: list[tuple[int, int]]) -> None: ...

    def _create_decompressor(self, point: SeekPoint) -> _SDT:
        self._comp_cursor = point.compressed_offset
        self._decomp_cursor = point.decompressed_offset
        return self._make_decompressor(point)

    def _decompress_chunk(self, chunk: bytes) -> bytes:
        data, units = self._decompressor.feed(chunk)
        self._on_completed_segments(units)
        return data

    def _flush_decompressor(self) -> bytes:
        data, units = self._decompressor.flush()
        self._on_completed_segments(units)
        return data

    def _is_decompressor_finished(self) -> bool:
        return self._decompressor.is_finished()

    def _build_index_backwards(
        self,
        last_known: SeekPoint,
        scan_fn: Callable[..., list[Any]],
        to_point: Callable[[Any], SeekPoint],
        warning_msg: str,
    ) -> tuple[list[SeekPoint], int | None]:
        """Backward scan → seek points + total decompressed size.

        ``scan_fn`` reads only index/trailer structures (no decompression). On a
        ``CorruptionError`` (e.g. valid-but-unparseable trailing data) it logs and
        returns an empty index so the stream falls back to sequential decoding.
        """
        file_size = self._inner.seek(0, io.SEEK_END)
        try:
            bounds = scan_fn(
                self._inner,
                file_size,
                stop_at=last_known.compressed_offset,
                start_decompressed_offset=last_known.decompressed_offset,
            )
        except CorruptionError as e:
            logger.warning(warning_msg, e)
            return [], None
        points = [
            to_point(b)
            for b in bounds
            if b.decompressed_start > last_known.decompressed_offset
        ]
        total: int | None = bounds[-1].decompressed_end if bounds else None
        return points, total
