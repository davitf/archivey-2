"""Seekable decompressor stream parameterized by a ``Decoder`` strategy.

One concrete :class:`DecompressorStream` owns the buffer, position, seek-point table,
and seek algorithm. Codecs plug in through the :class:`Decoder` protocol (feed / flush /
recreate / index discovery) — not by subclassing the stream.
"""

from __future__ import annotations

import bisect
import io
import os
from dataclasses import dataclass, field
from typing import (
    Any,
    BinaryIO,
    Callable,
    Protocol,
    Sequence,
    cast,
)

from archivey.diagnostics import DiagnosticCode, SeekIndexContext
from archivey.exceptions import CorruptionError, TruncatedError
from archivey.internal.diagnostics_collector import (
    DiagnosticCollector,
    resolve_collector,
)
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


@dataclass
class DecodeOut:
    """Bytes produced by a decoder step, plus any absolute seek points discovered."""

    data: bytes
    points: list[SeekPoint] = field(default_factory=list)


class Decoder(Protocol):
    """Codec strategy for :class:`DecompressorStream`."""

    def recreate(self, point: SeekPoint, inner: BinaryIO) -> Decoder: ...

    def feed(self, chunk: bytes) -> DecodeOut: ...

    def flush(self) -> DecodeOut: ...

    @property
    def finished(self) -> bool: ...

    @property
    def pending_error(self) -> BaseException | None: ...

    def clear_pending_error(self) -> None:
        """Clear :attr:`pending_error` after the stream has raised it (or on seek reset)."""
        ...

    def build_index(
        self, inner: BinaryIO, last_known: SeekPoint
    ) -> tuple[list[SeekPoint], int | None]: ...


class BaseDecoder:
    """Default decoder behavior: empty points, no pending error, no-op index build."""

    _pending_error: BaseException | None = None

    @property
    def pending_error(self) -> BaseException | None:
        return self._pending_error

    def clear_pending_error(self) -> None:
        self._pending_error = None

    def build_index(
        self, inner: BinaryIO, last_known: SeekPoint
    ) -> tuple[list[SeekPoint], int | None]:
        del inner, last_known
        return [], None


MakeDecoder = Callable[[SeekPoint, BinaryIO], Decoder]


def build_index_backwards(
    inner: BinaryIO,
    last_known: SeekPoint,
    scan_fn: Callable[..., list[Any]],
    to_point: Callable[[Any], SeekPoint],
    warning_msg: str,
    *,
    codec_name: str = "",
    collector: DiagnosticCollector | None = None,
    scan: str = "backwards_index",
) -> tuple[list[SeekPoint], int | None]:
    """Backward scan → seek points + total decompressed size.

    ``scan_fn`` reads only index/trailer structures (no decompression). On a
    ``CorruptionError`` (e.g. valid-but-unparseable trailing data) it emits
    ``SEEK_INDEX_DEGRADED`` and returns an empty index so the stream falls back to
    sequential decoding.
    """
    file_size = inner.seek(0, io.SEEK_END)
    try:
        bounds = scan_fn(
            inner,
            file_size,
            stop_at=last_known.compressed_offset,
            start_decompressed_offset=last_known.decompressed_offset,
        )
    except CorruptionError as e:
        message = warning_msg % (e,)
        resolve_collector(collector).emit(
            code=DiagnosticCode.SEEK_INDEX_DEGRADED,
            message=message,
            context=SeekIndexContext(
                codec=codec_name,
                scan=scan,
                error_type=type(e).__name__,
            ),
            logger=logger,
        )
        return [], None
    points = [
        to_point(b)
        for b in bounds
        if b.decompressed_start > last_known.decompressed_offset
    ]
    total: int | None = bounds[-1].decompressed_end if bounds else None
    return points, total


class DecompressorStream(ReadOnlyIOStream):
    """Seekable decompressor stream driven by a :class:`Decoder` strategy.

    ``readable``/``writable``/``write``/``readinto`` come from :class:`ReadOnlyIOStream`
    (``readinto`` is built on this class's ``read``).
    """

    def __init__(
        self,
        path: str | os.PathLike[str] | BinaryIO,
        *,
        make_decoder: MakeDecoder,
        collector: DiagnosticCollector | None = None,
        codec_name: str = "",
        seekable: bool = True,
    ) -> None:
        super().__init__()
        if isinstance(path, (str, os.PathLike)):
            self._inner: BinaryIO = open(os.fspath(path), "rb")
            self._should_close = True
        else:
            self._inner = cast("BinaryIO", ensure_bufferedio(path))
            self._should_close = False
        self._diagnostics_collector = collector
        self._codec_name = codec_name
        # Declared seek demand: without it, skip seek-point tables / index scans, but
        # still allow O(n) seeks from the origin (compressed TAR needs that for random
        # access even when MemberStreams.SEEKABLE was not declared).
        self._index_enabled = seekable
        self._seek_points: list[SeekPoint] = [SeekPoint(0, 0)]
        self._index_built = False
        self._index_build_attempted = False
        self._make_decoder = make_decoder
        self._decoder: Decoder = make_decoder(self._seek_points[0], self._inner)
        self._buffer = bytearray()
        self._eof = False
        self._pos = 0
        self._size: int | None = None

    def seekable(self) -> bool:
        return self._inner.seekable()

    def add_seek_points(self, points: Sequence[SeekPoint]) -> None:
        """Merge ``points`` into the sorted index, skipping duplicates.

        Pass points in ascending ``decompressed_offset`` order; the common in-order case
        is an O(1) append, out-of-order insertions fall back to bisect. No-ops when
        index construction was not declared (no seek-point table is built), except for
        refining the origin's ``compressed_offset`` / ``state`` (unix-compress header
        commit must apply even when the table is not built).

        Same-``decompressed_offset`` collisions:
        - Origin (offset 0) may always be refined in place (unix-compress header commit).
        - For other offsets, an exact duplicate is skipped; a *forward* refinement
          (same ``state``, ``compressed_offset`` moves forward) last-wins — unix-compress
          empty CLEAR segments legitimately re-emit the same decompressed offset at a
          later compressed resume point. Divergent ``state`` or a backwards
          ``compressed_offset`` still asserts (xz/lzip should filter those away).
        """
        for point in points:
            # Origin refinement always applies — resume must skip a committed header
            # even when seek-point indexing was not declared. Last-wins here is
            # intentional: unix-compress emits SeekPoint(0, HEADER_SIZE) to replace
            # the placeholder SeekPoint(0, 0).
            origin = self._seek_points[0]
            if (
                point.decompressed_offset == 0
                and origin.decompressed_offset == 0
                and (
                    origin.compressed_offset != point.compressed_offset
                    or origin.state is not point.state
                )
            ):
                self._seek_points[0] = point
                continue
            if not self._index_enabled:
                continue
            if point < self._seek_points[-1]:
                i = bisect.bisect_left(self._seek_points, point)
                if i < len(self._seek_points) and self._seek_points[i] == point:
                    self._resolve_same_offset_collision(i, point)
                    continue
                self._seek_points.insert(i, point)
            elif self._seek_points[-1] == point:
                self._resolve_same_offset_collision(len(self._seek_points) - 1, point)
                continue
            else:
                self._seek_points.append(point)

    def _resolve_same_offset_collision(self, index: int, point: SeekPoint) -> None:
        """Skip duplicates; allow forward compressed-offset refinement; else assert."""
        existing = self._seek_points[index]
        if (
            existing.compressed_offset == point.compressed_offset
            and existing.state is point.state
        ):
            return
        if (
            point.compressed_offset >= existing.compressed_offset
            and existing.state is point.state
        ):
            self._seek_points[index] = point
            return
        assert False, (
            "seek-point collision at the same decompressed_offset with "
            f"differing resume data: existing={existing!r} new={point!r}"
        )

    def _find_best_seek_point(self, pos: int) -> SeekPoint:
        """The last seek point with ``decompressed_offset <= pos``."""
        i = bisect.bisect_right(self._seek_points, SeekPoint(pos, 0)) - 1
        return self._seek_points[i]

    def _reset_to_seek_point(self, point: SeekPoint) -> None:
        self._inner.seek(point.compressed_offset)
        self._decoder = self._decoder.recreate(point, self._inner)
        self._decoder.clear_pending_error()
        self._buffer.clear()
        self._eof = False
        self._pos = point.decompressed_offset

    def _ingest_decode(self, out: DecodeOut) -> bytes:
        if out.points:
            self.add_seek_points(out.points)
        return out.data

    def _read_decompressed_chunk(self) -> bytes:
        chunk = self._inner.read(65536)
        if not chunk:
            self._eof = True
            leftover = self._ingest_decode(self._decoder.flush())
            if not self._decoder.finished:
                raise TruncatedError("File is truncated")
            self._size = self._pos + len(self._buffer) + len(leftover)
            self._index_built = True  # a forward scan to EOF is a complete index
            return leftover
        return self._ingest_decode(self._decoder.feed(chunk))

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
            data = self.readall()
        else:
            if len(self._buffer) < n and not self._eof:
                self._buffer.extend(self._read_decompressed_chunk())
            data = bytes(self._buffer[:n])
            del self._buffer[:n]
            self._pos += len(data)
        if not data:
            err = self._decoder.pending_error
            if err is not None:
                self._decoder.clear_pending_error()
                raise err
        return data

    def close(self) -> None:
        if self._should_close:
            self._inner.close()
        super().close()

    def _ensure_index_built(self) -> None:
        if not self._index_enabled or self._index_built or self._index_build_attempted:
            return
        inner_pos = self._inner.tell()
        new_points, new_size = self._decoder.build_index(
            self._inner, self._seek_points[-1]
        )
        self._index_build_attempted = True
        if new_points or new_size is not None:
            self._index_built = True
        if new_points:
            self.add_seek_points(new_points)
        if new_size is not None:
            self._size = new_size
        # build_index may have seeked _inner (e.g. lzip's backward trailer scan);
        # restore it so the decompressor's expected read position is still valid.
        if self._inner.tell() != inner_pos:
            self._inner.seek(inner_pos)

    def try_get_size(self) -> int | None:
        """The total decompressed size if cheaply available (via the index), else ``None``."""
        if self._size is not None:
            return self._size
        if not self._index_enabled or not self._inner.seekable():
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
