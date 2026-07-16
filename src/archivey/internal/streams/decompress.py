"""Forward-only codec decoders: zlib/deflate, Brotli, PPMd, BCJ, Deflate64.

Each is a thin :class:`BaseDecoder` adapter. Construction helpers return a concrete
:class:`~archivey.internal.streams.decompressor_stream.DecompressorStream` wrapping
the decoder — there are no per-codec stream subclasses.
"""

from __future__ import annotations

import os
import zlib
from typing import Any, BinaryIO

from archivey.internal.diagnostics_collector import DiagnosticCollector
from archivey.internal.streams.decompressor_stream import (
    BaseDecoder,
    DecodeOut,
    DecompressorStream,
    SeekPoint,
)


class ZlibDecoder(BaseDecoder):
    """Inflate a raw-deflate or zlib-wrapped stream via ``zlib.decompressobj``."""

    def __init__(self, wbits: int = -15) -> None:
        self._wbits = wbits
        self._decomp = zlib.decompressobj(wbits)

    def recreate(self, point: SeekPoint, inner: BinaryIO) -> ZlibDecoder:
        del point, inner
        return ZlibDecoder(self._wbits)

    def feed(self, chunk: bytes, max_length: int = -1) -> DecodeOut:
        # unconsumed_tail holds input not yet consumed under a prior max_length cap;
        # prepend it exactly once (mirrors gzip._GzipReader).
        data = self._decomp.unconsumed_tail + chunk
        if not data:
            return DecodeOut(b"")
        if max_length < 0:
            return DecodeOut(self._decomp.decompress(data))
        return DecodeOut(self._decomp.decompress(data, max_length))

    def flush(self) -> DecodeOut:
        if self._decomp.unconsumed_tail:
            out = self._decomp.decompress(self._decomp.unconsumed_tail)
            return DecodeOut(out + self._decomp.flush())
        return DecodeOut(self._decomp.flush())

    @property
    def finished(self) -> bool:
        return self._decomp.eof

    @property
    def needs_input(self) -> bool:
        return not self._decomp.unconsumed_tail


class BrotliDecoder(BaseDecoder):
    """Decode a raw Brotli stream via the ``brotli`` package's incremental decompressor.

    The ``brotli`` import is local because it's an optional dependency with no type stubs;
    the codec layer's ``_open_brotli`` gates on its presence before constructing this, so
    the import here always succeeds.

    Brotli ≥1.2.0 exposes ``process(..., output_buffer_limit=)`` and
    ``can_accept_more_data()`` (CVE-2025-6176 mitigation). The limit is block-granular
    (observed floor ~32 KiB), not a hard byte cap, but it stops a single ``process``
    from materializing multi-megabyte bombs on ``read(1)``.
    """

    def __init__(self) -> None:
        import brotli

        self._decomp: Any = brotli.Decompressor()
        self._pending = b""
        # True while a prior budgeted process may still have output to drain via
        # process(b"", output_buffer_limit=…).
        self._drain_budgeted = False
        self._supports_output_limit = callable(
            getattr(self._decomp, "can_accept_more_data", None)
        )

    def recreate(self, point: SeekPoint, inner: BinaryIO) -> BrotliDecoder:
        del point, inner
        return BrotliDecoder()

    def feed(self, chunk: bytes, max_length: int = -1) -> DecodeOut:
        data = self._pending + chunk
        self._pending = b""
        if max_length < 0 or not self._supports_output_limit:
            self._drain_budgeted = False
            if not data:
                return DecodeOut(b"")
            return DecodeOut(self._decomp.process(data))

        can_accept = bool(self._decomp.can_accept_more_data())
        if not can_accept:
            # Limit reached on a prior call: only empty process is legal until
            # can_accept_more_data() flips true again.
            self._pending = data
            out = self._decomp.process(b"", output_buffer_limit=max_length)
        elif data:
            out = self._decomp.process(data, output_buffer_limit=max_length)
        elif self._drain_budgeted:
            out = self._decomp.process(b"", output_buffer_limit=max_length)
        else:
            return DecodeOut(b"")

        finished = bool(self._decomp.is_finished())
        # Keep draining while output is flowing or the decoder refuses more input.
        self._drain_budgeted = (not finished) and (
            len(out) > 0 or not bool(self._decomp.can_accept_more_data())
        )
        return DecodeOut(out)

    def flush(self) -> DecodeOut:
        # Brotli decodes eagerly; there is nothing buffered to flush at EOF.
        return DecodeOut(b"")

    @property
    def finished(self) -> bool:
        return bool(self._decomp.is_finished())

    @property
    def needs_input(self) -> bool:
        if self._pending:
            return False
        if self._supports_output_limit and not bool(
            self._decomp.can_accept_more_data()
        ):
            return False
        return not self._drain_budgeted


class PpmdDecoder(BaseDecoder):
    """Decode a PPMd stream via ``pyppmd``.

    Variant 7 (``Ppmd7Decoder``) is the 7z var.H coder. Variant 8 (``Ppmd8Decoder``)
    is ZIP method 98 / WinZip ZIPX PPMd, which also carries a restore-method parameter.
    """

    def __init__(
        self,
        *,
        order: int,
        mem_size: int,
        variant: int = 7,
        restore_method: int = 0,
    ) -> None:
        import pyppmd

        self._order = order
        self._mem_size = mem_size
        self._variant = variant
        self._restore_method = restore_method
        if variant == 8:
            self._decomp: Any = pyppmd.Ppmd8Decoder(order, mem_size, restore_method)
        else:
            self._decomp = pyppmd.Ppmd7Decoder(order, mem_size)
        self._pending = b""

    def recreate(self, point: SeekPoint, inner: BinaryIO) -> PpmdDecoder:
        del point, inner
        return PpmdDecoder(
            order=self._order,
            mem_size=self._mem_size,
            variant=self._variant,
            restore_method=self._restore_method,
        )

    def feed(self, chunk: bytes, max_length: int = -1) -> DecodeOut:
        data = self._pending + chunk
        self._pending = b""
        if not data and max_length >= 0:
            # pyppmd retains unconsumed input internally when length-limited.
            if getattr(self._decomp, "needs_input", True):
                return DecodeOut(b"")
            length = max_length
            return DecodeOut(self._decomp.decode(b"", length))
        if not data:
            return DecodeOut(b"")
        length = -1 if max_length < 0 else max_length
        return DecodeOut(self._decomp.decode(data, length))

    def flush(self) -> DecodeOut:
        # 7z/ZIP PPMd streams sometimes need a trailing NUL to finish when the decoder
        # still reports needs_input at EOF (mirrors py7zr's PpmdDecompressor).
        if getattr(self._decomp, "needs_input", False) and not self._decomp.eof:
            return DecodeOut(self._decomp.decode(b"\0", -1))
        return DecodeOut(b"")

    @property
    def finished(self) -> bool:
        return bool(self._decomp.eof)

    @property
    def needs_input(self) -> bool:
        if self._pending:
            return False
        return bool(getattr(self._decomp, "needs_input", True))


class BcjDecoder(BaseDecoder):
    """Apply a ``pybcj`` BCJ branch filter to an already-decompressed byte stream."""

    def __init__(self, *, decoder_attr: str, unpack_size: int) -> None:
        import bcj

        self._decoder_attr = decoder_attr
        self._unpack_size = unpack_size
        self._produced = 0
        decoder_cls = getattr(bcj, decoder_attr)
        self._decomp: Any = decoder_cls(unpack_size)
        self._pending = b""

    def recreate(self, point: SeekPoint, inner: BinaryIO) -> BcjDecoder:
        del point, inner
        return BcjDecoder(
            decoder_attr=self._decoder_attr, unpack_size=self._unpack_size
        )

    def feed(self, chunk: bytes, max_length: int = -1) -> DecodeOut:
        data = self._pending + chunk
        self._pending = b""
        if not data:
            return DecodeOut(b"")
        if max_length >= 0 and len(data) > max_length:
            # BCJ is a filter (near 1:1); feed only what the caller budget allows and
            # retain the rest — bounds peak buffer without a native max_length API.
            self._pending = data[max_length:]
            data = data[:max_length]
        out = self._decomp.decode(data)
        self._produced += len(out)
        return DecodeOut(out)

    def flush(self) -> DecodeOut:
        out = self._decomp.decode(self._pending)
        self._pending = b""
        out2 = self._decomp.decode(b"")
        self._produced += len(out) + len(out2)
        return DecodeOut(out + out2)

    @property
    def finished(self) -> bool:
        return self._produced >= self._unpack_size

    @property
    def needs_input(self) -> bool:
        return not self._pending


class Deflate64Decoder(BaseDecoder):
    """Decode a Deflate64 stream via ``inflate64.Inflater``.

    ``inflate64`` has no output-size parameter: one ``inflate`` of a small
    highly-compressible feed can still allocate the full expansion. When the
    stream passes ``max_length >= 0``, feed compressed input in small steps
    (see ``_BUDGETED_FEED``) and retain any overshoot in ``_pending_out`` so
    ``read(n)`` peak buffers stay near the caller's budget.

    Feed-size tradeoff on a 100 MiB zeros Deflate64 bomb (per-call max_out /
    throughput): 1→514 B / ~320 MiB/s; 64→19 KiB / ~700 MiB/s; 256→70 KiB /
    ~710 MiB/s; 64 KiB→18 MiB / ~460 MiB/s. 64 keeps peaks under a 64 KiB
    read budget while recovering most of the speed of larger feeds.
    """

    # Compressed bytes per inflate() under a max_length budget. See class docstring.
    _BUDGETED_FEED = 64

    def __init__(self) -> None:
        import inflate64

        self._decomp: Any = inflate64.Inflater()
        self._pending = b""
        self._pending_out = b""

    def recreate(self, point: SeekPoint, inner: BinaryIO) -> Deflate64Decoder:
        del point, inner
        return Deflate64Decoder()

    def feed(self, chunk: bytes, max_length: int = -1) -> DecodeOut:
        data = self._pending + chunk
        self._pending = b""
        if max_length < 0:
            if self._pending_out:
                data = self._pending_out + (self._decomp.inflate(data) if data else b"")
                self._pending_out = b""
                return DecodeOut(data)
            if not data:
                return DecodeOut(b"")
            return DecodeOut(self._decomp.inflate(data))

        out = bytearray()
        if self._pending_out:
            take = min(len(self._pending_out), max_length)
            out += self._pending_out[:take]
            self._pending_out = self._pending_out[take:]
            if len(out) >= max_length:
                self._pending = data
                return DecodeOut(bytes(out))

        step = self._BUDGETED_FEED
        while data and len(out) < max_length:
            produced = self._decomp.inflate(data[:step])
            data = data[step:]
            room = max_length - len(out)
            if len(produced) > room:
                out += produced[:room]
                self._pending_out = produced[room:]
                break
            out += produced
        self._pending = data
        return DecodeOut(bytes(out))

    def flush(self) -> DecodeOut:
        # Flush remaining state with an empty feed (mirrors py7zr's Deflate64Decompressor).
        if self._pending_out:
            out = self._pending_out
            self._pending_out = b""
            if not self._decomp.eof:
                out += self._decomp.inflate(b"")
            return DecodeOut(out)
        if self._decomp.eof:
            return DecodeOut(b"")
        return DecodeOut(self._decomp.inflate(b""))

    @property
    def finished(self) -> bool:
        return bool(self._decomp.eof) and not self._pending_out

    @property
    def needs_input(self) -> bool:
        return not self._pending and not self._pending_out


def ZlibDecompressorStream(
    path: str | os.PathLike[str] | BinaryIO,
    wbits: int = -15,
) -> DecompressorStream:
    """Inflate a raw-deflate or zlib-wrapped stream (forward-only)."""
    return DecompressorStream(path, make_decoder=lambda _p, _i: ZlibDecoder(wbits))


def BrotliDecompressorStream(
    path: str | os.PathLike[str] | BinaryIO,
) -> DecompressorStream:
    """Decode a raw Brotli stream (forward-only)."""
    return DecompressorStream(path, make_decoder=lambda _p, _i: BrotliDecoder())


def PpmdDecompressorStream(
    path: str | os.PathLike[str] | BinaryIO,
    *,
    order: int,
    mem_size: int,
    variant: int = 7,
    restore_method: int = 0,
) -> DecompressorStream:
    """Decode a PPMd stream (forward-only).

    ``variant=7`` is 7z PPMd var.H; ``variant=8`` is ZIP method 98 (PPMd8).
    """
    return DecompressorStream(
        path,
        make_decoder=lambda _p, _i: PpmdDecoder(
            order=order,
            mem_size=mem_size,
            variant=variant,
            restore_method=restore_method,
        ),
        codec_name="ppmd",
    )


def BcjFilterStream(
    path: str | os.PathLike[str] | BinaryIO,
    *,
    decoder_attr: str,
    unpack_size: int,
    seekable: bool = False,
    collector: DiagnosticCollector | None = None,
) -> DecompressorStream:
    """Apply a ``pybcj`` BCJ branch filter (forward-only)."""
    del collector  # accepted for call-site uniformity; BCJ emits no diagnostics today
    return DecompressorStream(
        path,
        make_decoder=lambda _p, _i: BcjDecoder(
            decoder_attr=decoder_attr, unpack_size=unpack_size
        ),
        codec_name="bcj",
        seekable=seekable,
    )


def Deflate64DecompressorStream(
    path: str | os.PathLike[str] | BinaryIO,
) -> DecompressorStream:
    """Decode a Deflate64 stream (forward-only)."""
    return DecompressorStream(path, make_decoder=lambda _p, _i: Deflate64Decoder())
