"""Shared helpers for the Phase-2 stream-layer tests."""

from __future__ import annotations

import io
import lzma
import shutil
import struct
import subprocess
import zlib


class NonSeekableBytesIO(io.RawIOBase):
    """A ``BytesIO`` that reports (and behaves as) non-seekable, for forward-only tests."""

    def __init__(self, data: bytes) -> None:
        super().__init__()
        self._inner = io.BytesIO(data)

    def readable(self) -> bool:
        return True

    def read(self, n: int = -1, /) -> bytes:
        return self._inner.read(n)

    def readinto(self, b) -> int:  # type: ignore[override]  # test double; broad buffer type
        return self._inner.readinto(b)

    def seekable(self) -> bool:
        return False

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        raise io.UnsupportedOperation("seek")

    def tell(self, /) -> int:
        return self._inner.tell()


class CountingBytesIO(io.RawIOBase):
    """A seekable ``BytesIO`` that counts ``read`` calls and bytes read.

    Used to assert that seeking decompresses only the needed block(s) rather than the
    whole stream from the start.
    """

    def __init__(self, data: bytes) -> None:
        super().__init__()
        self._inner = io.BytesIO(data)
        self.read_calls = 0
        self.bytes_read = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def read(self, n: int = -1, /) -> bytes:
        data = self._inner.read(n)
        if data:
            self.read_calls += 1
            self.bytes_read += len(data)
        return data

    def readinto(self, b) -> int:  # type: ignore[override]  # test double; broad buffer type
        n = self._inner.readinto(b)
        if n:
            self.read_calls += 1
            self.bytes_read += n
        return n

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        return self._inner.seek(offset, whence)

    def tell(self, /) -> int:
        return self._inner.tell()


def make_lzip_member(data: bytes, dict_size_bits: int = 20) -> bytes:
    """Build one lzip member from ``data`` using stdlib ``lzma``.

    lzip omits the 13-byte LZMA_ALONE header from its raw stream; compress with
    FORMAT_ALONE (which includes it), strip the header, then wrap in the lzip
    header/trailer. (Ported from DEV's ``create_lzip_member`` test helper.)
    """
    filters: list[dict] = [
        {"id": lzma.FILTER_LZMA1, "dict_size": 1 << dict_size_bits, "lc": 3, "lp": 0, "pb": 2}
    ]
    compressed_alone = lzma.compress(data, format=lzma.FORMAT_ALONE, filters=filters)
    lzma_raw = compressed_alone[13:]
    header = b"LZIP" + bytes([1, dict_size_bits])
    member_total = len(header) + len(lzma_raw) + 20
    trailer = struct.pack("<IQQ", zlib.crc32(data) & 0xFFFFFFFF, len(data), member_total)
    return header + lzma_raw + trailer


def make_multi_member_lzip(parts: list[bytes], dict_size_bits: int = 20) -> bytes:
    return b"".join(make_lzip_member(p, dict_size_bits) for p in parts)


def make_multi_stream_xz(parts: list[bytes]) -> bytes:
    return b"".join(lzma.compress(p, format=lzma.FORMAT_XZ) for p in parts)


def xz_cli_available() -> bool:
    """Whether the ``xz`` CLI is on PATH (needed to build a multi-block XZ stream)."""
    return shutil.which("xz") is not None


def make_multiblock_xz(data: bytes, block_size: int) -> bytes:
    """Compress ``data`` into a *single* XZ stream split into multiple blocks.

    stdlib ``lzma`` always emits one block per stream, so the multi-block layout (which
    drives ``XzDecompressorStream``'s block-chain random-access path) is produced via the
    ``xz`` CLI's ``--block-size``. Guard callers with :func:`xz_cli_available`.
    """
    result = subprocess.run(
        ["xz", "-z", "-c", f"--block-size={block_size}"],
        input=data,
        capture_output=True,
        check=True,
    )
    return result.stdout


def lzma2_raw_filters() -> list[dict]:
    """A FORMAT_RAW LZMA2 filter spec, as a 7z folder coder would supply."""
    return [{"id": lzma.FILTER_LZMA2, "preset": 6}]


def compress_lzma2_raw(data: bytes) -> bytes:
    return lzma.compress(data, format=lzma.FORMAT_RAW, filters=lzma2_raw_filters())
