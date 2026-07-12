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


class BcjFilterStream(DecompressorStream[Any]):
    """Apply a ``pybcj`` BCJ branch filter to an already-decompressed byte stream.

    Used for 7z **LZMA1+BCJ** folders: combining LZMA1 and BCJ in one liblzma
    ``FORMAT_RAW`` chain can silently drop the final look-ahead bytes when LZMA1
    lacks an end-of-stream marker (common from the 7-Zip CLI). Staging LZMA1 via
    stdlib and BCJ via ``pybcj`` avoids that. LZMA2+BCJ stays on the stdlib path.

    ``unpack_size`` is the coder's output size from the 7z folder; ``pybcj`` uses it
    to flush look-ahead at the end of the stream. The ``bcj`` import is local — the
    7z reader gates presence before constructing this stream.
    """

    def __init__(
        self,
        path: str | os.PathLike[str] | BinaryIO,
        *,
        decoder_attr: str,
        unpack_size: int,
        seekable: bool = False,
    ) -> None:
        self._decoder_attr = decoder_attr
        self._unpack_size = unpack_size
        self._produced = 0
        super().__init__(path, codec_name="bcj", seekable=seekable)

    def _create_decompressor(self, point: SeekPoint) -> Any:
        import bcj

        del point  # BCJ has no mid-stream resume state; seekers restart from origin.
        self._produced = 0
        # Remaining expected output equals the full coder size when restarting from 0.
        decoder_cls = getattr(bcj, self._decoder_attr)
        return decoder_cls(self._unpack_size)

    def _decompress_chunk(self, chunk: bytes) -> bytes:
        out = self._decompressor.decode(chunk)
        self._produced += len(out)
        return out

    def _flush_decompressor(self) -> bytes:
        out = self._decompressor.decode(b"")
        self._produced += len(out)
        return out

    def _is_decompressor_finished(self) -> bool:
        return self._produced >= self._unpack_size


class Deflate64DecompressorStream(DecompressorStream[Any]):
    """Decode a Deflate64 stream via ``inflate64.Inflater``.

    Forward-only. The ``inflate64`` package exposes an incremental ``Inflater``
    (``inflate()`` / ``eof``) with no file-like open — same shape as Brotli/PPMd.
    """

    def _create_decompressor(self, point: SeekPoint) -> Any:
        import inflate64

        return inflate64.Inflater()

    def _decompress_chunk(self, chunk: bytes) -> bytes:
        return self._decompressor.inflate(chunk)

    def _flush_decompressor(self) -> bytes:
        # Flush remaining state with an empty feed (mirrors py7zr's Deflate64Decompressor).
        if self._decompressor.eof:
            return b""
        return self._decompressor.inflate(b"")

    def _is_decompressor_finished(self) -> bool:
        return bool(self._decompressor.eof)
