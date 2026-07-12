"""Concrete raw-stream decompressor backends: zlib/deflate and Brotli.

These build on the codec-agnostic ``DecompressorStream`` base in ``decompressor_stream.py``;
the segmented XZ / lzip backends live in ``xz.py`` / ``lzip.py``. Each is forward-only here —
random access, where available, is handled by the codec layer's optional accelerators.
"""

from __future__ import annotations

import os
import zlib
from typing import Any, BinaryIO

from archivey.internal.streams.decompressor_stream import DecompressorStream, SeekPoint


class ZlibDecompressorStream(DecompressorStream["zlib._Decompress"]):
    """Inflate a raw-deflate or zlib-wrapped stream.

    ``wbits`` selects the format: ``-15`` for raw deflate (ZIP/7z) and ``zlib.MAX_WBITS``
    for a zlib-wrapped stream. Forward-only here — random access for deflate is provided
    by the optional accelerators in the codec layer, not by this class.
    """

    def __init__(
        self,
        path: str | os.PathLike[str] | BinaryIO,
        wbits: int = -15,
    ) -> None:
        self._wbits = wbits
        super().__init__(path)

    def _create_decompressor(self, point: SeekPoint) -> "zlib._Decompress":
        return zlib.decompressobj(self._wbits)

    def _decompress_chunk(self, chunk: bytes) -> bytes:
        return self._decompressor.decompress(chunk)

    def _flush_decompressor(self) -> bytes:
        return self._decompressor.flush()

    def _is_decompressor_finished(self) -> bool:
        return self._decompressor.eof


class BrotliDecompressorStream(DecompressorStream[Any]):
    """Decode a raw Brotli stream via the ``brotli`` package's incremental decompressor.

    Forward-only: Brotli has no container framing or block index, so there are no seek
    points (the base class re-decodes from the start for a backward seek). The ``brotli``
    package exposes only an incremental ``Decompressor`` (``process()`` / ``is_finished()``)
    with no file-like ``open()``, unlike ``zstandard`` / ``lz4`` — hence this wrapper.

    The ``brotli`` import is local because it's an optional dependency with no type stubs;
    the codec layer's ``_open_brotli`` gates on its presence before constructing this, so
    the import here always succeeds.
    """

    def _create_decompressor(self, point: SeekPoint) -> Any:
        import brotli

        return brotli.Decompressor()

    def _decompress_chunk(self, chunk: bytes) -> bytes:
        return self._decompressor.process(chunk)

    def _flush_decompressor(self) -> bytes:
        # Brotli decodes eagerly; there is nothing buffered to flush at EOF.
        return b""

    def _is_decompressor_finished(self) -> bool:
        return self._decompressor.is_finished()


class PpmdDecompressorStream(DecompressorStream[Any]):
    """Decode a PPMd var.H stream via ``pyppmd.Ppmd7Decoder``.

    Forward-only. ``order`` / ``mem_size`` come from the 7z coder properties blob
    (5 or 7 bytes). The ``pyppmd`` import is local — the codec layer gates presence
    before constructing this stream.
    """

    def __init__(
        self,
        path: str | os.PathLike[str] | BinaryIO,
        *,
        order: int,
        mem_size: int,
    ) -> None:
        self._order = order
        self._mem_size = mem_size
        super().__init__(path, codec_name="ppmd")

    def _create_decompressor(self, point: SeekPoint) -> Any:
        import pyppmd

        return pyppmd.Ppmd7Decoder(self._order, self._mem_size)

    def _decompress_chunk(self, chunk: bytes) -> bytes:
        return self._decompressor.decode(chunk, -1)

    def _flush_decompressor(self) -> bytes:
        # 7z PPMd streams sometimes need a trailing NUL to finish when the decoder
        # still reports needs_input at EOF (mirrors py7zr's PpmdDecompressor).
        if (
            getattr(self._decompressor, "needs_input", False)
            and not self._decompressor.eof
        ):
            return self._decompressor.decode(b"\0", -1)
        return b""

    def _is_decompressor_finished(self) -> bool:
        return bool(self._decompressor.eof)
