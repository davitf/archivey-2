"""Seekable decompressor stream base class and format-specific subclasses."""

import abc
import bisect
import io
import logging
import os
import zlib
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    BinaryIO,
    Callable,
    Generic,
    Protocol,
    TypeVar,
    cast,
)

if TYPE_CHECKING:
    from typing_extensions import Buffer
else:
    # See compressed_streams.py: `Buffer` is annotation-only; keep the core
    # import dependency-free with a runtime fallback.
    try:
        from typing_extensions import Buffer
    except ImportError:  # pragma: no cover - bare core install
        Buffer = bytes

from archivey.exceptions import ArchiveCorruptedError, ArchiveEOFError
from archivey.internal.streams.compat import ensure_bufferedio

if TYPE_CHECKING:
    import brotli

    from archivey.types import ReadableBinaryStream
else:
    try:
        import brotli
    except ImportError:
        brotli = None

logger = logging.getLogger(__name__)


@dataclass(order=True)
class SeekPoint:
    """A point in a compressed stream from which decompression can resume.

    Ordered by decompressed_offset only, so bisect operations on a
    list[SeekPoint] work without a key= argument.
    """

    decompressed_offset: int
    compressed_offset: int = field(compare=False)
    state: Any = field(default=None, compare=False)


DecompressorT = TypeVar("DecompressorT")


class _SegmentDecompressor(Protocol):
    """Interface shared by _LzipState, _XzState, and _XzBlockChain."""

    def feed(self, data: bytes) -> tuple[bytes, list[tuple[int, int]]]: ...
    def flush(self) -> tuple[bytes, list[tuple[int, int]]]: ...
    def is_finished(self) -> bool: ...


_SDT = TypeVar("_SDT", bound=_SegmentDecompressor)


class DecompressorStream(io.RawIOBase, BinaryIO, Generic[DecompressorT]):
    """Seekable decompressor stream with optional seek-point-based random access.

    Subclasses implement the four abstract methods to provide the actual
    decompression.  Subclasses that support efficient random access may also:

    - Call add_seek_points() from _decompress_chunk/_flush_decompressor to
      register known positions as they are discovered during forward reads.
    - Override _build_index() for a one-shot full index build (e.g. lzip reads
      member trailers backwards).  _build_index is called at most once.
    """

    def __init__(self, path: "str | BinaryIO | ReadableBinaryStream") -> None:
        super().__init__()
        self._inner: io.BufferedIOBase
        if isinstance(path, (str, bytes, os.PathLike)):
            self._inner = open(path, "rb")
            self._should_close = True
        else:
            self._inner = ensure_bufferedio(path)
            self._should_close = False
        self._seek_points: list[SeekPoint] = [SeekPoint(0, 0)]
        self._index_built: bool = False
        self._index_build_attempted: bool = False
        self._decompressor: DecompressorT = self._create_decompressor(
            self._seek_points[0]
        )
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
        """Flush pending data from the decompressor and return it.

        Called exactly once when the compressed input is exhausted.  Most
        decompressors decode eagerly and return b"" here.  zlib is an
        exception: its flush() processes all remaining buffered input and
        returns the last portion of decompressed data.  After this call the
        decompressor must not be used again.
        """
        ...

    @abc.abstractmethod
    def _is_decompressor_finished(self) -> bool: ...

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:  # pragma: no cover - not used
        return False

    def seekable(self) -> bool:
        return self._inner.seekable()

    def add_seek_points(self, points: list[SeekPoint]) -> None:
        """Merge seek points into the sorted index, skipping duplicates.

        Callers should pass points in ascending decompressed_offset order.
        The common path (new point is after all existing ones) is O(1) append;
        out-of-order insertions fall back to bisect + insert.
        """
        for point in points:
            if point < self._seek_points[-1]:
                # Out-of-order (should be rare): bisect insert
                i = bisect.bisect_left(self._seek_points, point)
                if i < len(self._seek_points) and self._seek_points[i] == point:
                    continue  # duplicate
                self._seek_points.insert(i, point)
            elif self._seek_points[-1] == point:
                continue  # duplicate
            else:
                self._seek_points.append(point)  # fast path: in-order append

    def _find_best_seek_point(self, pos: int) -> SeekPoint:
        """Return the last seek point with decompressed_offset <= pos."""
        # i >= 0 because SeekPoint(0, 0) is always the first entry and pos >= 0
        i = bisect.bisect_right(self._seek_points, SeekPoint(pos, 0)) - 1
        return self._seek_points[i]

    def _reset_to_seek_point(self, point: SeekPoint) -> None:
        """Jump to a known seek point.  Does not touch _size."""
        self._inner.seek(point.compressed_offset)
        self._decompressor = self._create_decompressor(point)
        self._buffer.clear()
        self._eof = False
        self._pos = point.decompressed_offset

    def _build_index(self, last_known: SeekPoint) -> tuple[list[SeekPoint], int | None]:
        """One-shot full index build.  Default: no-op, returns empty list and no size.

        Subclasses that support random access override this to return a list of
        new seek points and the total decompressed size (or None if unknown).
        Called at most once per stream (guarded by _index_built in seek()).

        last_known: the highest-offset SeekPoint currently in _seek_points.
        Subclasses may use it to start forward indexing from there or to stop
        a backwards scan at that point.

        The inner stream's position after this call is unspecified; seek()
        repositions it via _reset_to_seek_point as needed.
        """
        return [], None

    def _read_decompressed_chunk(self) -> bytes:
        chunk = self._inner.read(65536)
        if not chunk:
            self._eof = True
            leftover = self._flush_decompressor()
            logger.info("EOF reached, leftover: %d", len(leftover))
            if not self._is_decompressor_finished():
                raise ArchiveEOFError("File is truncated")
            self._size = self._pos + len(self._buffer) + len(leftover)
            self._index_built = True  # forward scan to EOF implies complete index
            logger.info("EOF reached, size: %d", self._size)
            return leftover
        return self._decompress_chunk(chunk)

    def readall(self) -> bytes:
        while not self._eof:
            self._buffer.extend(self._read_decompressed_chunk())

        data = bytes(self._buffer)
        self._buffer.clear()
        # _pos may be past _size when the caller seeked beyond EOF; in that case
        # the buffer is already empty and we must not overwrite _size with _pos.
        if self._size is None or self._pos <= self._size:
            self._pos += len(data)
            self._size = self._pos
        return data

    def read(self, n: int = -1) -> bytes:
        if n == 0:
            return b""
        if n is None or n < 0:
            return self.readall()

        if len(self._buffer) < n and not self._eof:
            # Read only one more block
            self._buffer.extend(self._read_decompressed_chunk())

        data = bytes(self._buffer[:n])
        del self._buffer[:n]
        self._pos += len(data)
        return data

    def readinto(self, b: Buffer) -> int:
        mv = memoryview(b).cast("B")
        data = self.read(len(mv))
        mv[: len(data)] = data
        return len(data)

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

        # _build_index may seek _inner for index reads (e.g. lzip's
        # backward trailer scan); restore it so the decompressor's
        # expected read position is still valid.
        if self._inner.tell() != inner_pos:
            self._inner.seek(inner_pos)

    def try_get_size(self) -> int | None:
        """Return the total decompressed size if cheaply available, else None.

        Attempts to build the index (backward scan) to learn the size without
        falling back to decompressing the whole stream.  Safe to call on open;
        returns None rather than blocking if the index scan fails.
        """
        if self._size is not None:
            return self._size
        if not self._inner.seekable():
            return None
        self._ensure_index_built()
        return self._size

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if not self._inner.seekable():
            raise io.UnsupportedOperation("seek")

        if whence == io.SEEK_SET:
            new_pos = offset
        elif whence == io.SEEK_CUR:
            new_pos = self._pos + offset
        elif whence == io.SEEK_END:
            new_pos = -1  # sentinel; resolved below after determining _size
        else:
            raise ValueError(f"Invalid whence: {whence}")

        # Build the index if we're seeking from the end (as we need to know the
        # total decompressed size) or to a target posiiton after the current
        # buffer end and after the last known seek point.
        if whence == io.SEEK_END or (
            new_pos > self._pos + len(self._buffer)
            and new_pos > self._seek_points[-1].decompressed_offset
        ):
            self._ensure_index_built()

        if whence == io.SEEK_END:
            if self._size is None:
                # If we don't know the stream size (building the index above
                # doesn't always provide it), scan to EOF to discover it.
                # We don't use readall() to avoid buffering all the remaining
                # data in RAM.
                self._pos += len(self._buffer)
                self._buffer.clear()
                while not self._eof:
                    data = self._read_decompressed_chunk()
                    self._pos += len(data)
                # _read_decompressed_chunk() sets _size when it reaches the
                # end of the stream.
                assert self._size is not None

            new_pos = self._size + offset

        if new_pos < 0:
            raise ValueError(f"Invalid offset: {offset}")

        # Short-circuit when past EOF
        if self._size is not None and new_pos >= self._size:
            self._buffer.clear()
            self._eof = True
            self._pos = new_pos
            return self._pos

        if new_pos == self._pos:
            return self._pos

        if new_pos < self._pos:
            # Backward seek: jump to the nearest seek point, then forward-read
            self._reset_to_seek_point(self._find_best_seek_point(new_pos))
        elif new_pos <= self._pos + len(self._buffer):
            # Target is inside the current look-ahead buffer
            del self._buffer[: new_pos - self._pos]
            self._pos = new_pos
            return self._pos
        else:
            # Forward seek past the current buffer: jump to a closer seek point
            # if one exists, otherwise advance past the buffered data.
            best = self._find_best_seek_point(new_pos)
            if best.decompressed_offset > self._pos:
                self._reset_to_seek_point(best)
            else:
                self._pos += len(self._buffer)
                self._buffer.clear()

        # Buffer is empty after any reset or explicit clear above.
        assert not self._buffer
        if self._pos == new_pos:
            return self._pos

        # Forward-read loop (handles tail of backward seeks and forward jumps)
        while not self._eof:
            decompressed = self._read_decompressed_chunk()
            if self._pos + len(decompressed) >= new_pos:
                self._buffer.extend(decompressed[new_pos - self._pos :])
                self._pos = new_pos
                return self._pos
            self._pos += len(decompressed)

        # Past EOF
        self._pos = new_pos
        return self._pos

    def tell(self) -> int:
        return self._pos


class _SegmentedDecompressorStream(DecompressorStream[_SDT]):
    """Intermediate base for multi-segment compressed formats (lzip, XZ).

    Factors out cursor tracking, the feed/flush delegation to the state-machine
    decompressor, and a shared _build_index_backwards skeleton.  Subclasses
    implement _make_decompressor, _on_completed_segments, and _build_index.
    """

    def __init__(self, path: "str | BinaryIO | ReadableBinaryStream") -> None:
        # Pre-declare cursors before super().__init__() because the base
        # calls _create_decompressor() which reads them.
        self._comp_cursor: int = 0
        self._decomp_cursor: int = 0
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
        scan_fn: "Callable[..., list[Any]]",
        to_point: "Callable[[Any], SeekPoint]",
        warning_msg: str,
    ) -> "tuple[list[SeekPoint], int | None]":
        """Shared skeleton: backward scan → SeekPoints + total decompressed size.

        Calls scan_fn(inner, file_size, stop_at=..., start_decompressed_offset=...),
        converts each bounds object to a SeekPoint via to_point (skipping any
        whose decompressed_start duplicates last_known), and infers total size
        from the last bound's decompressed_end.
        """
        file_size = self._inner.seek(0, io.SEEK_END)
        try:
            bounds = scan_fn(
                cast("BinaryIO", self._inner),
                file_size,
                stop_at=last_known.compressed_offset,
                start_decompressed_offset=last_known.decompressed_offset,
            )
        except ArchiveCorruptedError as e:
            logger.warning(warning_msg, e)
            return [], None
        points = [
            to_point(b)
            for b in bounds
            if b.decompressed_start > last_known.decompressed_offset
        ]
        total: "int | None" = bounds[-1].decompressed_end if bounds else None
        return points, total


class ZlibDecompressorStream(DecompressorStream["zlib._Decompress"]):
    def _create_decompressor(self, point: SeekPoint) -> "zlib._Decompress":
        return zlib.decompressobj()

    def _decompress_chunk(self, chunk: bytes) -> bytes:
        return self._decompressor.decompress(chunk)

    def _flush_decompressor(self) -> bytes:
        return self._decompressor.flush()

    def _is_decompressor_finished(self) -> bool:
        return self._decompressor.eof


class BrotliDecompressorStream(DecompressorStream["brotli.Decompressor"]):
    """Wrap a file-like object and decompress it using ``brotli``."""

    def _create_decompressor(self, point: SeekPoint) -> "brotli.Decompressor":
        return brotli.Decompressor()

    def _decompress_chunk(self, chunk: bytes) -> bytes:
        return cast("bytes", self._decompressor.process(chunk))

    def _flush_decompressor(self) -> bytes:
        # brotli's decompressor doesn't have a flush method.
        return b""

    def _is_decompressor_finished(self) -> bool:
        return cast("bool", self._decompressor.is_finished())
