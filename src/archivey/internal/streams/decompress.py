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

    def feed(self, chunk: bytes) -> DecodeOut:
        return DecodeOut(self._decomp.decompress(chunk))

    def flush(self) -> DecodeOut:
        return DecodeOut(self._decomp.flush())

    @property
    def finished(self) -> bool:
        return self._decomp.eof


class BrotliDecoder(BaseDecoder):
    """Decode a raw Brotli stream via the ``brotli`` package's incremental decompressor.

    The ``brotli`` import is local because it's an optional dependency with no type stubs;
    the codec layer's ``_open_brotli`` gates on its presence before constructing this, so
    the import here always succeeds.
    """

    def __init__(self) -> None:
        import brotli

        self._decomp: Any = brotli.Decompressor()

    def recreate(self, point: SeekPoint, inner: BinaryIO) -> BrotliDecoder:
        del point, inner
        return BrotliDecoder()

    def feed(self, chunk: bytes) -> DecodeOut:
        return DecodeOut(self._decomp.process(chunk))

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

    ``unpack_size`` (when known, e.g. 7z folder unpack size) is passed through as
    ``max_length`` on every ``decode`` call. PPMd7 has no end mark: ``decode(..., -1)``
    can overshoot the true payload and stress the native allocator; bounding matches
    py7zr's ``PpmdDecompressor.decompress(..., max_length)``.

    At compressed EOF, PPMd may still need an extra NUL input byte when the encoder
    omitted a trailing null (documented by pyppmd). That path feeds one NUL with the
    remaining ``max_length``, same as py7zr / the pyppmd PyPI sample.
    """

    def __init__(
        self,
        *,
        order: int,
        mem_size: int,
        variant: int = 7,
        restore_method: int = 0,
        unpack_size: int | None = None,
    ) -> None:
        import pyppmd

        self._order = order
        self._mem_size = mem_size
        self._variant = variant
        self._restore_method = restore_method
        self._unpack_size = unpack_size
        self._produced = 0
        if variant == 8:
            self._decomp: Any = pyppmd.Ppmd8Decoder(order, mem_size, restore_method)
        else:
            self._decomp = pyppmd.Ppmd7Decoder(order, mem_size)

    def recreate(self, point: SeekPoint, inner: BinaryIO) -> PpmdDecoder:
        del point, inner
        return PpmdDecoder(
            order=self._order,
            mem_size=self._mem_size,
            variant=self._variant,
            restore_method=self._restore_method,
            unpack_size=self._unpack_size,
        )

    def _max_length(self) -> int:
        if self._unpack_size is None:
            return -1
        return max(0, self._unpack_size - self._produced)

    def _decode(self, data: bytes, max_length: int) -> bytes:
        if max_length == 0:
            return b""
        # Empty input + needs_input: feed the PPMd "extra" NUL (pyppmd / py7zr).
        if (
            not data
            and getattr(self._decomp, "needs_input", False)
            and not self._decomp.eof
        ):
            return self._decomp.decode(b"\0", max_length)
        return self._decomp.decode(data, max_length)

    def feed(self, chunk: bytes) -> DecodeOut:
        out = self._decode(chunk, self._max_length())
        self._produced += len(out)
        return DecodeOut(out)

    def flush(self) -> DecodeOut:
        # Drain with extra NULs while the decoder still wants input and we have room
        # under unpack_size (or unbound when size is unknown). Cap iterations so a
        # stuck needs_input cannot loop forever.
        parts: list[bytes] = []
        for _ in range(8):
            max_length = self._max_length()
            if max_length == 0:
                break
            if self._decomp.eof or not getattr(self._decomp, "needs_input", False):
                break
            chunk = self._decomp.decode(b"\0", max_length)
            if not chunk:
                break
            parts.append(chunk)
            self._produced += len(chunk)
        return DecodeOut(b"".join(parts))

    @property
    def finished(self) -> bool:
        if self._unpack_size is not None and self._produced >= self._unpack_size:
            return True
        return bool(self._decomp.eof)


class BcjDecoder(BaseDecoder):
    """Apply a ``pybcj`` BCJ branch filter to an already-decompressed byte stream."""

    def __init__(self, *, decoder_attr: str, unpack_size: int) -> None:
        import bcj

        self._decoder_attr = decoder_attr
        self._unpack_size = unpack_size
        self._produced = 0
        decoder_cls = getattr(bcj, decoder_attr)
        self._decomp: Any = decoder_cls(unpack_size)

    def recreate(self, point: SeekPoint, inner: BinaryIO) -> BcjDecoder:
        del point, inner
        return BcjDecoder(
            decoder_attr=self._decoder_attr, unpack_size=self._unpack_size
        )

    def feed(self, chunk: bytes) -> DecodeOut:
        out = self._decomp.decode(chunk)
        self._produced += len(out)
        return DecodeOut(out)

    def flush(self) -> DecodeOut:
        out = self._decomp.decode(b"")
        self._produced += len(out)
        return DecodeOut(out)

    @property
    def finished(self) -> bool:
        return self._produced >= self._unpack_size


class Deflate64Decoder(BaseDecoder):
    """Decode a Deflate64 stream via ``inflate64.Inflater``."""

    def __init__(self) -> None:
        import inflate64

        self._decomp: Any = inflate64.Inflater()

    def recreate(self, point: SeekPoint, inner: BinaryIO) -> Deflate64Decoder:
        del point, inner
        return Deflate64Decoder()

    def feed(self, chunk: bytes) -> DecodeOut:
        return DecodeOut(self._decomp.inflate(chunk))

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
    unpack_size: int | None = None,
) -> DecompressorStream:
    """Decode a PPMd stream (forward-only).

    ``variant=7`` is 7z PPMd var.H; ``variant=8`` is ZIP method 98 (PPMd8).
    Pass ``unpack_size`` when known (7z folder size) so PPMd7 decode calls are bounded.
    """
    return DecompressorStream(
        path,
        make_decoder=lambda _p, _i: PpmdDecoder(
            order=order,
            mem_size=mem_size,
            variant=variant,
            restore_method=restore_method,
            unpack_size=unpack_size,
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
