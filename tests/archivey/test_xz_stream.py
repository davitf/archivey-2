"""Tests for XzDecompressorStream: multi-stream files and efficient seeking."""

import io
import lzma

import pytest

from archivey.exceptions import ArchiveCorruptedError, ArchiveEOFError
from archivey.formats.compressed_streams import _translate_xz_exception
from archivey.internal.streams.xz import XzDecompressorStream, _read_xz_index_backwards

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_multi_stream(parts: list[bytes]) -> bytes:
    """Concatenate multiple XZ streams into one byte string."""
    return b"".join(lzma.compress(p, format=lzma.FORMAT_XZ) for p in parts)


class _CountingStream(io.RawIOBase):
    """Wraps a BytesIO and counts read() calls and total bytes read."""

    def __init__(self, data: bytes) -> None:
        super().__init__()
        self._inner = io.BytesIO(data)
        self.read_calls = 0
        self.bytes_read = 0

    def readable(self) -> bool:
        return True

    def readinto(self, b: bytearray | memoryview) -> int:
        n = self._inner.readinto(b)
        if n:
            self.read_calls += 1
            self.bytes_read += n
        return n

    def read(self, n: int = -1) -> bytes:  # type: ignore[override]
        data = self._inner.read(n)
        if data:
            self.read_calls += 1
            self.bytes_read += len(data)
        return data

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        return self._inner.seek(offset, whence)

    def tell(self) -> int:
        return self._inner.tell()

    def seekable(self) -> bool:
        return True


class _NonSeekableStream(io.RawIOBase):
    """Non-seekable wrapper for testing sequential-only mode."""

    def __init__(self, data: bytes) -> None:
        super().__init__()
        self._inner = io.BytesIO(data)

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def read(self, n: int = -1) -> bytes:  # type: ignore[override]
        return self._inner.read(n)

    def readinto(self, b: bytearray | memoryview) -> int:
        data = self._inner.read(len(b))
        n = len(data)
        b[:n] = data
        return n


def open_xz(data: bytes) -> XzDecompressorStream:
    return XzDecompressorStream(io.BytesIO(data))


def open_xz_counting(data: bytes) -> tuple[XzDecompressorStream, _CountingStream]:
    cs = _CountingStream(data)
    stream = XzDecompressorStream(io.BufferedReader(cs))
    return stream, cs


# ---------------------------------------------------------------------------
# Basic multi-stream reading
# ---------------------------------------------------------------------------


def test_single_stream_read():
    payload = b"hello world"
    data = make_multi_stream([payload])
    with open_xz(data) as f:
        assert f.read() == payload


def test_multi_stream_read():
    parts = [b"first stream", b"second stream", b"third stream"]
    data = make_multi_stream(parts)
    expected = b"".join(parts)
    with open_xz(data) as f:
        assert f.read() == expected


def test_multi_stream_read_chunked():
    parts = [b"aaa" * 100, b"bbb" * 100, b"ccc" * 100]
    data = make_multi_stream(parts)
    expected = b"".join(parts)
    with open_xz(data) as f:
        result = bytearray()
        while True:
            chunk = f.read(50)
            if not chunk:
                break
            result.extend(chunk)
    assert bytes(result) == expected


# ---------------------------------------------------------------------------
# Non-seekable stream
# ---------------------------------------------------------------------------


def test_non_seekable_stream_read_succeeds():
    payload = b"non-seekable content" * 100
    data = lzma.compress(payload, format=lzma.FORMAT_XZ)
    ns = _NonSeekableStream(data)
    with XzDecompressorStream(ns) as f:
        assert not f.seekable()
        assert f.read() == payload


def test_non_seekable_stream_seek_raises():
    data = lzma.compress(b"hello", format=lzma.FORMAT_XZ)
    ns = _NonSeekableStream(data)
    with XzDecompressorStream(ns) as f:
        assert not f.seekable()
        with pytest.raises(io.UnsupportedOperation):
            f.seek(0, io.SEEK_END)


# ---------------------------------------------------------------------------
# Stream padding between XZ streams
# ---------------------------------------------------------------------------


def test_stream_padding_between_streams():
    part1 = b"first part"
    part2 = b"second part"
    stream1 = lzma.compress(part1, format=lzma.FORMAT_XZ)
    stream2 = lzma.compress(part2, format=lzma.FORMAT_XZ)
    # 4-byte-aligned null padding between streams
    data = stream1 + b"\x00" * 8 + stream2
    with open_xz(data) as f:
        assert f.read() == part1 + part2


# ---------------------------------------------------------------------------
# Trailing non-XZ bytes are silently ignored
# ---------------------------------------------------------------------------


def test_trailing_non_xz_bytes_ignored():
    payload = b"valid content"
    data = lzma.compress(payload, format=lzma.FORMAT_XZ) + b"\xff\xfe\xfd garbage"
    with open_xz(data) as f:
        assert f.read() == payload


def test_trailing_non_xz_bytes_size_correct():
    payload = b"valid content"
    compressed = lzma.compress(payload, format=lzma.FORMAT_XZ)
    data = compressed + b"\xff\xfe\xfd garbage"
    with open_xz(data) as f:
        size = f.seek(0, io.SEEK_END)
        assert size == len(payload)


# ---------------------------------------------------------------------------
# SEEK_END
# ---------------------------------------------------------------------------


def test_seek_end_single_stream():
    payload = b"hello world" * 100
    data = make_multi_stream([payload])
    with open_xz(data) as f:
        size = f.seek(0, io.SEEK_END)
        assert size == len(payload)


def test_seek_end_multi_stream():
    parts = [b"hello", b"world", b"!"]
    data = make_multi_stream(parts)
    expected_size = sum(len(p) for p in parts)
    with open_xz(data) as f:
        size = f.seek(0, io.SEEK_END)
        assert size == expected_size


def test_seek_end_does_not_decompress():
    """SEEK_END should use the backwards index scan, not decompress any data."""
    parts = [b"first" * 100, b"second" * 100, b"third" * 100]
    data = make_multi_stream(parts)
    with open_xz(data) as f:
        f.seek(0, io.SEEK_END)
        # _decomp_cursor == 0 confirms the backward scan decompressed nothing.
        assert f._decomp_cursor == 0  # type: ignore[attr-defined]
        assert f._index_built  # type: ignore[attr-defined]


def test_seek_end_then_read_last_bytes():
    parts = [b"first", b"second", b"last!"]
    data = make_multi_stream(parts)
    with open_xz(data) as f:
        f.seek(-5, io.SEEK_END)
        assert f.read() == b"last!"


def test_seek_end_negative_offset_into_earlier_stream():
    parts = [b"AAAA", b"BBBB"]
    data = make_multi_stream(parts)
    # Total = 8 bytes. -6 → position 2 (2 bytes into "AAAA")
    with open_xz(data) as f:
        f.seek(-6, io.SEEK_END)
        assert f.read() == b"AABBBB"


def test_seek_past_eof_then_read_returns_empty():
    parts = [b"hello", b"world"]
    data = make_multi_stream(parts)
    with open_xz(data) as f:
        size = f.seek(0, io.SEEK_END)
        f.seek(1, io.SEEK_END)
        assert f.read() == b""
        assert f.seek(0, io.SEEK_END) == size


# ---------------------------------------------------------------------------
# Forward seeking
# ---------------------------------------------------------------------------


def test_single_stream_seek_forward():
    payload = b"abcdefghij"
    data = make_multi_stream([payload])
    with open_xz(data) as f:
        f.seek(3)
        assert f.read(4) == b"defg"


def test_multi_stream_seek_forward_into_later_stream():
    parts = [b"AAAA", b"BBBB", b"CCCC"]
    data = make_multi_stream(parts)
    with open_xz(data) as f:
        f.seek(8)
        assert f.read(4) == b"CCCC"


def test_multi_stream_seek_then_read_across_stream_boundary():
    """Seeking to a block-level seek point and reading across a stream boundary.

    _XzBlockChain transitions between blocks from different streams (they are
    NOT contiguous on disk — index/footer/stream-header bytes intervene).
    _start_block() must seek the inner file to each block's compressed_start
    rather than assuming the bytes immediately follow the previous block.
    """
    stream0 = bytes(i % 256 for i in range(8000))
    stream1 = bytes((255 - i % 256) for i in range(8000))
    stream2 = bytes((i * 3) % 256 for i in range(8000))
    parts = [stream0, stream1, stream2]
    data = make_multi_stream(parts)
    with open_xz(data) as f:
        f.seek(0, io.SEEK_END)  # build index; creates block-level seek points
        # Seek to the start of stream1 — this has a block-level seek point
        # so _XzBlockChain is used.  read() reads all remaining streams.
        f.seek(len(stream0))
        assert f._decompressor.__class__.__name__ == "_XzBlockChain"  # type: ignore[attr-defined]
        result = f.read()  # readall — crosses stream1 → stream2 boundary
        assert result == stream1 + stream2


def test_forward_seek_uses_seek_points():
    """After building index, forward seek to a stream boundary uses seek points."""
    parts = [b"stream0" * 50, b"stream1" * 50, b"stream2" * 50]
    data = make_multi_stream(parts)
    with open_xz(data) as f:
        # Build index via SEEK_END
        f.seek(0, io.SEEK_END)
        assert f._index_built  # type: ignore[attr-defined]

        # Seek back to start, then forward directly to stream 2
        f.seek(0)
        target = len(parts[0]) + len(parts[1])
        f.seek(target)
        assert f._decomp_cursor == target  # type: ignore[attr-defined]
        assert f.read() == parts[2]


# ---------------------------------------------------------------------------
# Backward seeking
# ---------------------------------------------------------------------------


def test_single_stream_seek_backward():
    payload = b"abcdefghij"
    data = make_multi_stream([payload])
    with open_xz(data) as f:
        f.read()
        f.seek(3)
        assert f.read(4) == b"defg"


def test_multi_stream_seek_backward_to_earlier_stream():
    parts = [b"AAAA", b"BBBB", b"CCCC"]
    data = make_multi_stream(parts)
    with open_xz(data) as f:
        f.read()
        f.seek(0)
        assert f.read(4) == b"AAAA"
        f.seek(4)
        assert f.read(4) == b"BBBB"


def test_backward_seek_uses_index_not_position_zero():
    """After reading to stream 2, seeking backward to stream 1 should use a seek
    point, not rewind to compressed byte 0."""
    parts = [b"stream0" * 100, b"stream1" * 100, b"stream2" * 100]
    data = make_multi_stream(parts)
    with open_xz(data) as f:
        # Read all streams so the index covers all
        f.read()

        target = len(parts[0])
        f.seek(target)
        # _decomp_cursor confirms we jumped to stream 1, not stream 0
        assert f._decomp_cursor == target  # type: ignore[attr-defined]
        assert f.read(len(parts[1])) == parts[1]


# ---------------------------------------------------------------------------
# _read_xz_index_backwards — unit tests
# ---------------------------------------------------------------------------


def test_read_xz_index_backwards_single_stream():
    payload = b"hello world" * 100
    data = lzma.compress(payload, format=lzma.FORMAT_XZ)
    stream = io.BytesIO(data)
    blocks = _read_xz_index_backwards(stream, len(data))
    assert len(blocks) >= 1
    total_decompressed = sum(b.uncompressed_size for b in blocks)
    assert total_decompressed == len(payload)


def test_read_xz_index_backwards_multi_stream():
    parts = [b"first stream content", b"second stream content"]
    data = make_multi_stream(parts)
    stream = io.BytesIO(data)
    blocks = _read_xz_index_backwards(stream, len(data))
    total_decompressed = sum(b.uncompressed_size for b in blocks)
    assert total_decompressed == sum(len(p) for p in parts)
    # Blocks should be in forward order
    for i in range(len(blocks) - 1):
        assert blocks[i].compressed_start < blocks[i + 1].compressed_start
        assert blocks[i].decompressed_start < blocks[i + 1].decompressed_start


def test_read_xz_index_backwards_block_offsets():
    payload = b"x" * 5000
    data = lzma.compress(payload, format=lzma.FORMAT_XZ)
    stream = io.BytesIO(data)
    blocks = _read_xz_index_backwards(stream, len(data))
    # Decompressed offsets should accumulate correctly
    offset = 0
    for block in blocks:
        assert block.decompressed_start == offset
        offset += block.uncompressed_size


def test_read_xz_index_backwards_stream_padding():
    part1 = b"before padding"
    part2 = b"after padding"
    stream1 = lzma.compress(part1, format=lzma.FORMAT_XZ)
    stream2 = lzma.compress(part2, format=lzma.FORMAT_XZ)
    data = stream1 + b"\x00" * 4 + stream2
    stream = io.BytesIO(data)
    blocks = _read_xz_index_backwards(stream, len(data))
    total = sum(b.uncompressed_size for b in blocks)
    assert total == len(part1) + len(part2)


def test_read_xz_index_backwards_corrupt_footer_magic():
    data = bytearray(lzma.compress(b"hello", format=lzma.FORMAT_XZ))
    # Corrupt the last 2 bytes (footer magic 'YZ')
    data[-2] = 0xFF
    data[-1] = 0xFF
    stream = io.BytesIO(bytes(data))
    with pytest.raises(ArchiveCorruptedError):
        _read_xz_index_backwards(stream, len(data))


def test_read_xz_index_backwards_corrupt_index_crc():
    data = bytearray(lzma.compress(b"hello world", format=lzma.FORMAT_XZ))
    # Corrupt the CRC32 at the end of the index (4 bytes before the 12-byte footer)
    # Footer is last 12 bytes; index CRC is 4 bytes before that
    crc_offset = len(data) - 12 - 4
    data[crc_offset] ^= 0xFF
    stream = io.BytesIO(bytes(data))
    with pytest.raises(ArchiveCorruptedError):
        _read_xz_index_backwards(stream, len(data))


# ---------------------------------------------------------------------------
# Corruption detection
# ---------------------------------------------------------------------------


def test_truncation_mid_stream_raises_eof_error():
    data = lzma.compress(b"hello world", format=lzma.FORMAT_XZ)
    truncated = data[: len(data) // 2]
    with open_xz(truncated) as f:
        with pytest.raises((ArchiveEOFError, ArchiveCorruptedError)):
            f.read()


def test_empty_file_raises_corrupted_error():
    with open_xz(b"") as f:
        with pytest.raises(ArchiveCorruptedError):
            f.read()


def test_non_xz_data_raises_corrupted_error():
    gzip_magic = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03"
    with open_xz(gzip_magic) as f:
        with pytest.raises(ArchiveCorruptedError):
            f.read()


# ---------------------------------------------------------------------------
# Exception translation
# ---------------------------------------------------------------------------


def test_translate_xz_exception_wraps_lzma_error():
    result = _translate_xz_exception(lzma.LZMAError("bad data"))
    assert isinstance(result, ArchiveCorruptedError)


def test_translate_xz_exception_wraps_eof_error():
    result = _translate_xz_exception(EOFError("truncated"))
    assert isinstance(result, ArchiveEOFError)


# ---------------------------------------------------------------------------
# SingleFileReader file_size for multi-stream XZ and lzip
# ---------------------------------------------------------------------------


def test_single_file_reader_multi_stream_xz_file_size():
    """SingleFileReader must report the sum of all streams' decompressed sizes."""
    import os
    import tempfile

    import archivey

    part1 = b"stream one content " * 100
    part2 = b"stream two content " * 100
    data = make_multi_stream([part1, part2])

    with tempfile.NamedTemporaryFile(suffix=".xz", delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name

    try:
        with archivey.open_archive(tmp_path) as archive:
            members = archive.get_members()
            assert len(members) == 1
            assert members[0].file_size == len(part1) + len(part2)
    finally:
        os.unlink(tmp_path)


def test_single_file_reader_lzip_file_size():
    """SingleFileReader must now populate file_size for lzip archives."""
    import os
    import tempfile

    from tests.archivey.create_archives import create_lzip_member

    import archivey

    payload = b"lzip content " * 100
    data = create_lzip_member(payload)

    with tempfile.NamedTemporaryFile(suffix=".lz", delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name

    try:
        with archivey.open_archive(tmp_path) as archive:
            members = archive.get_members()
            assert len(members) == 1
            assert members[0].file_size == len(payload)
    finally:
        os.unlink(tmp_path)
