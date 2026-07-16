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

    Brotli's ``process()`` has no output cap — a tiny compressed feed can still expand
    unboundedly. The stream layer's smaller compressed reads limit how much we *feed*
    per call, but peak memory for pathological brotli remains a residual.
    """

    def __init__(self) -> None:
        import brotli

        self._decomp: Any = brotli.Decompressor()
        self._pending = b""

    def recreate(self, point: SeekPoint, inner: BinaryIO) -> BrotliDecoder:
        del point, inner
        return BrotliDecoder()

    def feed(self, chunk: bytes, max_length: int = -1) -> DecodeOut:
        del max_length  # brotli cannot honor an output budget
        data = self._pending + chunk
        self._pending = b""
        if not data:
            return DecodeOut(b"")
        return DecodeOut(self._decomp.process(data))

    def flush(self) -> DecodeOut:
        # Brotli decodes eagerly; there is nothing buffered to flush at EOF.
        return DecodeOut(b"")

    @property
    def finished(self) -> bool:
        return bool(self._decomp.is_finished())


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
    """Decode a Deflate64 stream via ``inflate64.Inflater``."""

    def __init__(self) -> None:
        import inflate64

        self._decomp: Any = inflate64.Inflater()
        self._pending = b""

    def recreate(self, point: SeekPoint, inner: BinaryIO) -> Deflate64Decoder:
        del point, inner
        return Deflate64Decoder()

    def feed(self, chunk: bytes, max_length: int = -1) -> DecodeOut:
        del max_length  # inflate64 has no output cap
        data = self._pending + chunk
        self._pending = b""
        if not data:
            return DecodeOut(b"")
        return DecodeOut(self._decomp.inflate(data))

    def flush(self) -> DecodeOut:
        # Flush remaining state with an empty feed (mirrors py7zr's Deflate64Decompressor).
        if self._decomp.eof:
            return DecodeOut(b"")
        return DecodeOut(self._decomp.inflate(b""))

    @property
    def finished(self) -> bool:
        return bool(self._decomp.eof)


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
