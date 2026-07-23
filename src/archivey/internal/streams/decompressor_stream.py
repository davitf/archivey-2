"""Seekable decompress *engine*: one stream class, many codec strategies.

:class:`DecompressorStream` owns the buffer, position, seek-point table, and seek
algorithm. Codecs plug in through the :class:`Decoder` protocol (feed / flush /
recreate / index discovery) — not by subclassing the stream.

Where the decoders live (easy to mix with this file's name):

- :mod:`archivey.internal.streams.decompress` — zlib/deflate, Brotli, PPMd, BCJ,
  Deflate64 adapters
- :mod:`archivey.internal.streams.xz` / ``lzip`` / ``unix_compress`` — larger
  format-specific decoders (index scan / LZW)

``codecs.StreamCodec.open`` wires those into an ``ArchiveStream``; this module is
only the shared engine underneath.
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

    def feed(self, chunk: bytes, max_length: int = -1) -> DecodeOut:
        """Decode ``chunk``, producing at most ``max_length`` output bytes when ≥ 0.

        When ``max_length`` limits output, unconsumed compressed input must be retained
        so a subsequent ``feed(b"", max_length=…)`` (while :attr:`needs_input` is false)
        can continue without the stream reading more from the source. ``max_length=-1``
        means unlimited (used by ``readall`` / flush-to-EOF).
        """
        ...

    def flush(self) -> DecodeOut:
        """Finalize at compressed EOF — the sole truncation-detection point.

        Called exactly once when the compressed source is exhausted. Implementations
        MUST arm :attr:`pending_error` with a :class:`~archivey.exceptions.TruncatedError`
        when the decode is incomplete (not :attr:`finished`, or finished alongside a
        known truncation such as unix-compress leftover bits), and MUST return any
        recoverable flush leftover rather than raising that truncation inline.
        :class:`~archivey.exceptions.CorruptionError` for trailing junk / hard
        corruption MAY still raise from ``flush``.
        """
        ...

    @property
    def finished(self) -> bool: ...

    @property
    def needs_input(self) -> bool:
        """False when more output can be produced without reading new compressed bytes."""
        ...

    @property
    def pending_error(self) -> BaseException | None: ...

    def clear_pending_error(self) -> None:
        """Clear :attr:`pending_error` after the stream has raised it (or on seek reset)."""
        ...

    def close(self) -> None:
        """Release decoder-owned native resources deterministically.

        Optional teardown hook: most decoders need nothing here (GC frees the
        underlying object), but a decoder holding a native worker whose lifetime
        is unsafe under GC (PPMd) uses this to reach a clean state before it is
        dropped. Idempotent; never raises.
        """
        ...

    def build_index(
        self, inner: BinaryIO, last_known: SeekPoint
    ) -> tuple[list[SeekPoint], int | None]: ...


class BaseDecoder:
    """Default decoder behavior: empty points, no pending error, no-op index build.

    Subclasses that override :meth:`flush` own truncation detection: at compressed
    EOF, arm :attr:`pending_error` when the stream is incomplete and return any
    leftover bytes — do not raise :class:`~archivey.exceptions.TruncatedError` from
    ``flush`` itself (the stream raises it on the next empty ``read``, or from
    ``readall``).
    """

    _pending_error: BaseException | None = None

    @property
    def pending_error(self) -> BaseException | None:
        return self._pending_error

    def clear_pending_error(self) -> None:
        self._pending_error = None

    @property
    def needs_input(self) -> bool:
        return True

    def close(self) -> None:
        """No-op teardown hook (see :meth:`Decoder.close`); overridden by PPMd."""

    def build_index(
        self, inner: BinaryIO, last_known: SeekPoint
    ) -> tuple[list[SeekPoint], int | None]:
        del inner, last_known
        return [], None


# Compressed bytes read per fill when the decoder needs more input.
#
# Historically matched CPython gzip / ``_compression.DecompressReader``
# (``io.DEFAULT_BUFFER_SIZE`` = 8 KiB). That feed forces ~17 Python trips through
# the decode loop for a typical 256 KiB ZIP member while ``zipfile`` decompresses
# each member in one C call — the dominant residual ZIP read-all gap after the
# stream-layering work in #136/#137 (see ``review/performance/residual-gap.md``).
# 64 KiB reaches the measured plateau for those members; ``max_length`` still
# bounds peak *output* buffer on ``read(n)`` (the #128 / F3a contract), and ZIP
# members are additionally capped by their ``SlicingStream`` compressed extent.
_COMPRESSED_READ_SIZE = 65536
# Ceiling when a large bounded ``read(n)`` (whole-member via fused verify) asks
# for more output than the default feed — one compressed read ≈ one C inflate.
_COMPRESSED_READ_SIZE_MAX = 1 << 20
# Output budget when skipping forward during seek (unbounded skip would reintroduce
# the per-read amplification bomb on highly compressible spans).
_SEEK_OUTPUT_CHUNK = 65536


def _compressed_feed_size(max_length: int) -> int:
    """How many compressed bytes to pull for one decoder feed.

    Large bounded requests scale up toward ``max_length`` (capped) so a known-size
    whole-member read collapses to one inflate call. Small / unbounded requests
    keep the default feed; output amplification remains gated by ``max_length``.
    """
    if max_length < 0 or max_length <= _COMPRESSED_READ_SIZE:
        return _COMPRESSED_READ_SIZE
    return min(max_length, _COMPRESSED_READ_SIZE_MAX)


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
    include_block: Callable[[Any], bool] | None = None,
) -> tuple[list[SeekPoint], int | None]:
    """Backward scan → seek points + total decompressed size.

    ``scan_fn`` reads only index/trailer structures (no decompression). On a
    ``CorruptionError`` (e.g. valid-but-unparseable trailing data) it emits
    ``SEEK_INDEX_DEGRADED`` and returns an empty index so the stream falls back to
    sequential decoding.

    ``include_block``, when set, filters scanned bounds before they become seek
    points (e.g. XZ zero-``uncompressed_size`` blocks that share a decompressed
    offset with the next real block and are never useful resume targets). The
    total size still comes from the last bound's ``decompressed_end``.
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
        and (include_block is None or include_block(b))
    ]
    total: int | None = bounds[-1].decompressed_end if bounds else None
    return points, total


class DecompressorStream(ReadOnlyIOStream):
    """Seekable ``BinaryIO`` over compressed bytes, driven by a :class:`Decoder`.

    Owns: output buffer, logical position, seek-point table, and the seek algorithm
    (bisect to a point → recreate decoder → skip forward). Does **not** know codec
    formats — ``make_decoder`` supplies that.

    ``seekable=False`` skips index/seek-point work (forward-only cheap path).
    ``readable``/``writable``/``write``/``readinto`` come from :class:`ReadOnlyIOStream`.
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
          later compressed resume point. A ``state=None`` placeholder yields to a richer
          non-``None`` resume state (XZ block bounds over a progressive stream-start).
          Divergent non-``None`` states raise :class:`CorruptionError` (never a raw
          ``AssertionError``) so hostile indexes cannot escape the ``ArchiveyError`` tree.
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
        """Skip duplicates; allow forward refinement / richer-state merge; else error."""
        existing = self._seek_points[index]

        def _same_state(a: object, b: object) -> bool:
            # Identity for shared objects; value equality for re-emitted XZ block bounds
            # (progressive enrichment vs build_index construct distinct instances).
            return a is b or a == b

        if existing.compressed_offset == point.compressed_offset and _same_state(
            existing.state, point.state
        ):
            return
        if point.compressed_offset >= existing.compressed_offset and _same_state(
            existing.state, point.state
        ):
            self._seek_points[index] = point
            return
        # Prefer a non-None resume state over a progressive placeholder (XZ: block
        # bounds beat a stream-start SeekPoint emitted before enrichment / after a
        # prior build_index). Same-offset with only one side carrying state is the
        # legitimate multi-stream path; keep the richer point.
        if existing.state is None and point.state is not None:
            self._seek_points[index] = point
            return
        if existing.state is not None and point.state is None:
            return
        raise CorruptionError(
            "seek-point collision at the same decompressed_offset with "
            f"differing resume data: existing={existing!r} new={point!r}"
        )

    def _find_best_seek_point(self, pos: int) -> SeekPoint:
        """The last seek point with ``decompressed_offset <= pos``."""
        i = bisect.bisect_right(self._seek_points, SeekPoint(pos, 0)) - 1
        return self._seek_points[i]

    def _reset_to_seek_point(self, point: SeekPoint) -> None:
        self._inner.seek(point.compressed_offset)
        # Dispose the outgoing decoder deterministically before dropping it:
        # mid-member a PPMd decode can leave its native worker parked, and relying
        # on __del__/GC timing to quiesce it is exactly what close() exists to avoid
        # (no-op for every other codec). recreate() builds a fresh decoder from the
        # config, not from the old native state, so closing first is safe.
        old_decoder = self._decoder
        old_decoder.close()
        self._decoder = old_decoder.recreate(point, self._inner)
        self._decoder.clear_pending_error()
        self._buffer.clear()
        self._eof = False
        self._pos = point.decompressed_offset

    def _ingest_decode(self, out: DecodeOut) -> bytes:
        if out.points:
            self.add_seek_points(out.points)
        return out.data

    def _read_decompressed_chunk(self, max_length: int = -1) -> bytes:
        if not self._decoder.needs_input:
            drained = self._ingest_decode(self._decoder.feed(b"", max_length))
            if drained:
                return drained
            # Decoder claimed retained input but produced nothing (e.g. a stuck
            # lzma needs_input=False under a budget). Fall through to reading more
            # compressed bytes — or EOF — so the caller cannot spin forever.
        chunk = self._inner.read(_compressed_feed_size(max_length))
        if not chunk:
            self._eof = True
            leftover = self._ingest_decode(self._decoder.flush())
            # Incomplete EOF: decoder owns TruncatedError via pending_error (set in
            # flush). Deliver leftover now; bounded read raises on the next empty
            # read. Only publish a clean complete size when truly finished and not
            # truncated (pending_error alone is insufficient — unix-compress can be
            # finished=True with leftover-bits truncation).
            if self._decoder.pending_error is None and self._decoder.finished:
                self._size = self._pos + len(self._buffer) + len(leftover)
                self._index_built = True  # a forward scan to EOF is a complete index
            return leftover
        return self._ingest_decode(self._decoder.feed(chunk, max_length))

    def readall(self) -> bytes:
        # Prefer join-of-chunks over staging through the shared bytearray: a whole-stream
        # read never needs the partial-read buffer, and the extend + bytes(buffer) copy
        # was a measurable share of ZIP read-all overhead (perf review H2).
        chunks: list[bytes] = []
        if self._buffer:
            chunks.append(bytes(self._buffer))
            self._buffer.clear()
        while not self._eof:
            chunk = self._read_decompressed_chunk()
            if chunk:
                chunks.append(chunk)
        data = b"".join(chunks)
        # A read(-1)/readall() caller expects the complete stream and will not call
        # again, so a deferred pending_error (e.g. truncated .Z) must raise here —
        # unlike chunked read(n), which returns bytes now and raises on the next empty
        # read. Partial bytes from this call are dropped: the caller asked for the
        # whole stream and it is incomplete. Gate _size *before* raising so a caller
        # that catches TruncatedError cannot then read a clean prefix-as-complete size.
        err = self._decoder.pending_error
        if err is not None:
            self._decoder.clear_pending_error()
            raise err
        if self._size is None or self._pos <= self._size:
            self._pos += len(data)
            self._size = self._pos
        return data

    def read(self, n: int = -1, /) -> bytes:
        if n == 0:
            return b""
        if n is None or n < 0:
            return self.readall()
        while len(self._buffer) < n and not self._eof:
            need = n - len(self._buffer)
            self._buffer.extend(self._read_decompressed_chunk(need))
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
        # Quiesce any decoder-owned native worker before dropping references, so a
        # blocked PPMd worker cannot be resumed into freed memory at GC.
        self._decoder.close()
        if self._should_close:
            self._inner.close()
        super().close()

    def _ensure_index_built(self) -> None:
        if not self._index_enabled or self._index_built or self._index_build_attempted:
            return
        inner_pos = self._inner.tell()
        # Always scan from the absolute origin. Using a mid-stream last_known (from
        # progressive enrichment) as the baseline renumbers later streams' decompressed
        # offsets incorrectly. A full from-origin scan is cheap (index/trailer only) and
        # makes block-chain resume safe after a partial forward read.
        new_points, new_size = self._decoder.build_index(self._inner, SeekPoint(0, 0))
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
                    data = self._read_decompressed_chunk(_SEEK_OUTPUT_CHUNK)
                    self._pos += len(data)
                # Truncated streams must not publish a clean complete size; surface
                # the deferred fault instead of asserting or treating the prefix as
                # the full stream.
                err = self._decoder.pending_error
                if err is not None:
                    self._decoder.clear_pending_error()
                    raise err
                if self._size is None:
                    raise TruncatedError(
                        "Cannot seek to end: decompressed size is unknown "
                        "(stream ended incompletely)"
                    )
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
            self._reset_to_seek_point(self._prepare_seek_point(new_pos))
        elif new_pos <= self._pos + len(self._buffer):
            del self._buffer[: new_pos - self._pos]
            self._pos = new_pos
            return self._pos
        else:
            best = self._prepare_seek_point(new_pos)
            if best.decompressed_offset > self._pos:
                self._reset_to_seek_point(best)
            else:
                self._pos += len(self._buffer)
                self._buffer.clear()

        assert not self._buffer
        if self._pos == new_pos:
            return self._pos

        while not self._eof:
            decompressed = self._read_decompressed_chunk(_SEEK_OUTPUT_CHUNK)
            if self._pos + len(decompressed) >= new_pos:
                self._buffer.extend(decompressed[new_pos - self._pos :])
                self._pos = new_pos
                return self._pos
            self._pos += len(decompressed)

        self._pos = new_pos
        return self._pos

    def _prepare_seek_point(self, pos: int) -> SeekPoint:
        """Best resume point for ``pos``, with a complete index if block-state is used.

        Progressive enrichment only adds points for *completed* streams. Resuming via
        an ``_XzBlockBounds`` point builds a closed block chain from that point plus
        already-indexed later blocks; if later streams are not indexed yet the chain
        finishes early and the stream silently EOFs. Force a full from-origin index
        before any stateful resume so the chain includes every subsequent block.
        """
        best = self._find_best_seek_point(pos)
        if best.state is not None and not self._index_built:
            self._ensure_index_built()
            best = self._find_best_seek_point(pos)
        return best

    def tell(self, /) -> int:
        return self._pos
