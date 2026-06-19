"""Tests for LzipDecompressorStream: multi-member files and efficient seeking."""

import io
import lzma
import struct
from typing import TYPE_CHECKING, BinaryIO, cast

import pytest

from archivey.exceptions import ArchiveCorruptedError, ArchiveEOFError
from archivey.formats.compressed_streams import _translate_lzip_exception
from archivey.formats.lzip_stream import LzipDecompressorStream, _read_index_backwards
from tests.archivey.create_archives import create_lzip_member

if TYPE_CHECKING:
    from archivey.formats.decompressor_stream import SeekPoint

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_multi_member(parts: list[bytes], dict_size_bits: int = 12) -> bytes:
    """Concatenate multiple lzip members into one byte string."""
    return b"".join(create_lzip_member(p, dict_size_bits) for p in parts)


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


class _LimitedReadStream(io.BufferedIOBase):
    """Wraps BytesIO and caps each read() to at most max_read_size bytes.

    Used in tests to prevent DecompressorStream from consuming multiple
    lzip members' compressed data in a single _read_decompressed_chunk() call.
    Since it extends BufferedIOBase, ensure_bufferedio() returns it as-is.
    """

    def __init__(self, data: bytes, max_read_size: int) -> None:
        super().__init__()
        self._inner = io.BytesIO(data)
        self._max = max_read_size

    def readable(self) -> bool:
        return True

    def read(self, n: int = -1) -> bytes:  # type: ignore[override]
        if n is None or n < 0:
            return self._inner.read()
        return self._inner.read(min(n, self._max))

    def read1(self, n: int = -1) -> bytes:
        return self.read(n)

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        return self._inner.seek(offset, whence)

    def tell(self) -> int:
        return self._inner.tell()

    def seekable(self) -> bool:
        return True


def open_lzip(data: bytes) -> LzipDecompressorStream:
    return LzipDecompressorStream(io.BytesIO(data))


def open_lzip_counting(data: bytes) -> tuple[LzipDecompressorStream, _CountingStream]:
    cs = _CountingStream(data)
    stream = LzipDecompressorStream(io.BufferedReader(cs))
    return stream, cs


# ---------------------------------------------------------------------------
# Basic multi-member reading
# ---------------------------------------------------------------------------


def test_single_member_read():
    payload = b"hello world"
    data = make_multi_member([payload])
    with open_lzip(data) as f:
        assert f.read() == payload


def test_multi_member_read():
    parts = [b"first member", b"second member", b"third member"]
    data = make_multi_member(parts)
    expected = b"".join(parts)
    with open_lzip(data) as f:
        assert f.read() == expected


def test_multi_member_read_chunked():
    parts = [b"aaa" * 100, b"bbb" * 100, b"ccc" * 100]
    data = make_multi_member(parts)
    expected = b"".join(parts)
    with open_lzip(data) as f:
        result = bytearray()
        while True:
            chunk = f.read(50)
            if not chunk:
                break
            result.extend(chunk)
    assert bytes(result) == expected


# ---------------------------------------------------------------------------
# Forward seeking
# ---------------------------------------------------------------------------


def test_single_member_seek_forward():
    payload = b"abcdefghij"
    data = make_multi_member([payload])
    with open_lzip(data) as f:
        f.seek(3)
        assert f.read(4) == b"defg"


def test_multi_member_seek_forward_into_later_member():
    parts = [b"AAAA", b"BBBB", b"CCCC"]
    data = make_multi_member(parts)
    with open_lzip(data) as f:
        # Seek past the first member into the third
        f.seek(8)
        assert f.read(4) == b"CCCC"


def test_forward_seek_skips_intermediate_members():
    """After building the index, seeking forward past indexed members doesn't
    decompress those members — verified by checking completed_members count."""
    parts = [b"member0" * 10, b"member1" * 10, b"member2" * 10]
    data = make_multi_member(parts)
    with open_lzip(data) as f:
        # Build the full index via SEEK_END
        f.seek(0, io.SEEK_END)
        assert f._index_built  # type: ignore[attr-defined]

        # Seek back to start, then forward directly to member 2
        f.seek(0)
        target = len(parts[0]) + len(parts[1])
        f.seek(target)
        # _decomp_cursor == target confirms we jumped directly to member 2's start;
        # members 0 and 1 were not decompressed.
        assert f._decomp_cursor == target  # type: ignore[attr-defined]
        assert f.read() == parts[2]


# ---------------------------------------------------------------------------
# Backward seeking
# ---------------------------------------------------------------------------


def test_single_member_seek_backward():
    payload = b"abcdefghij"
    data = make_multi_member([payload])
    with open_lzip(data) as f:
        f.read()  # advance to EOF
        f.seek(3)
        assert f.read(4) == b"defg"


def test_multi_member_seek_backward_to_earlier_member():
    parts = [b"AAAA", b"BBBB", b"CCCC"]
    data = make_multi_member(parts)
    with open_lzip(data) as f:
        f.read()  # read all three members (builds index)
        f.seek(0)
        assert f.read(4) == b"AAAA"
        f.seek(4)
        assert f.read(4) == b"BBBB"


def test_backward_seek_uses_index_not_position_zero():
    """After reading to member 2, seeking backward to member 1 should jump
    directly to member 1's compressed offset, not rewind to compressed byte 0."""
    parts = [b"member0" * 20, b"member1" * 20, b"member2" * 20]
    data = make_multi_member(parts)
    with open_lzip(data) as f:
        # Read through all members so the index covers 0..2
        f.read()
        assert len(f._seek_points) == 3  # type: ignore[attr-defined]

        # Now seek backward to the start of member 1
        target = len(parts[0])
        f.seek(target)
        # _decomp_cursor == target confirms we jumped directly to member 1 (not 0)
        assert f._decomp_cursor == target  # type: ignore[attr-defined]
        assert f.read(len(parts[1])) == parts[1]


# ---------------------------------------------------------------------------
# SEEK_END
# ---------------------------------------------------------------------------


def test_seek_end_returns_correct_size():
    parts = [b"hello", b"world", b"!"]
    data = make_multi_member(parts)
    expected_size = sum(len(p) for p in parts)
    with open_lzip(data) as f:
        size = f.seek(0, io.SEEK_END)
        assert size == expected_size


def test_seek_end_does_not_decompress():
    """SEEK_END should use the backwards trailer scan, not decompress any data."""
    parts = [b"first" * 100, b"second" * 100, b"third" * 100]
    data = make_multi_member(parts)
    with open_lzip(data) as f:
        f.seek(0, io.SEEK_END)
        # _decomp_cursor == 0 confirms the backward scan decompressed nothing.
        assert f._decomp_cursor == 0  # type: ignore[attr-defined]
        assert f._index_built  # type: ignore[attr-defined]


def test_seek_end_then_read_last_bytes():
    parts = [b"first", b"second", b"last!"]
    data = make_multi_member(parts)
    with open_lzip(data) as f:
        f.seek(-5, io.SEEK_END)
        assert f.read() == b"last!"


def test_seek_end_negative_offset_into_earlier_member():
    parts = [b"AAAA", b"BBBB"]
    data = make_multi_member(parts)
    # Total = 8 bytes. -6 → position 2 (2 bytes into "AAAA")
    with open_lzip(data) as f:
        f.seek(-6, io.SEEK_END)
        assert f.read() == b"AABBBB"


def test_seek_past_eof_then_read_returns_empty():
    """Seeking past EOF and then reading must return b"" without asserting."""
    parts = [b"hello", b"world"]
    data = make_multi_member(parts)
    with open_lzip(data) as f:
        size = f.seek(0, io.SEEK_END)
        # Seek one byte past EOF
        f.seek(1, io.SEEK_END)
        assert f.read() == b""
        # Size must not have been overwritten by the past-EOF pos
        assert f.seek(0, io.SEEK_END) == size


def test_forward_seek_triggers_index_build():
    """Seeking forward past the known index frontier should trigger a backwards scan."""
    parts = [b"part0" * 5, b"part1" * 5, b"part2" * 5]
    member_bytes = [create_lzip_member(p) for p in parts]
    data = b"".join(member_bytes)
    # Limit each compressed read to one member's worth of data so that members
    # are indexed one at a time (the default 65536-byte read would index them all
    # at once for this small test data, making _index_complete True immediately).
    chunk_size = max(len(m) for m in member_bytes)
    with LzipDecompressorStream(
        cast("BinaryIO", _LimitedReadStream(data, chunk_size))
    ) as f:
        # Read only the first member's content
        f.read(len(parts[0]))
        assert not f._index_built  # type: ignore[attr-defined]
        # Seek past what we've indexed — should trigger backwards scan
        target = len(parts[0]) + len(parts[1])
        f.seek(target)
        assert f._index_built  # type: ignore[attr-defined]
        assert f.read() == parts[2]


# ---------------------------------------------------------------------------
# Member index building
# ---------------------------------------------------------------------------


def test_member_index_built_progressively():
    parts = [b"part0", b"part1", b"part2"]
    member_bytes = [create_lzip_member(p) for p in parts]
    data = b"".join(member_bytes)
    # Limit compressed reads to one member at a time so the index grows step-by-step.
    chunk_size = max(len(m) for m in member_bytes)
    with LzipDecompressorStream(
        cast("BinaryIO", _LimitedReadStream(data, chunk_size))
    ) as f:
        # Origin point SeekPoint(0, 0) is always present
        assert len(f._seek_points) == 1  # type: ignore[attr-defined]

        # After member 0 completes, _update_index adds SeekPoint(0, 0) which is
        # a duplicate of the initial origin point, so the count stays at 1.
        f.read(len(parts[0]))
        assert len(f._seek_points) == 1  # type: ignore[attr-defined]

        f.read(len(parts[1]))
        assert len(f._seek_points) == 2  # type: ignore[attr-defined]

        f.read()
        assert len(f._seek_points) == 3  # type: ignore[attr-defined]


def test_member_index_bounds():
    parts = [b"aaa", b"bb", b"c"]
    data = make_multi_member(parts)
    with open_lzip(data) as f:
        f.read()
        sp: list[SeekPoint] = f._seek_points  # type: ignore[attr-defined]
        assert sp[0].decompressed_offset == 0
        assert sp[0].compressed_offset == 0
        assert sp[1].decompressed_offset == 3
        assert sp[2].decompressed_offset == 5
        # Consecutive seek points encode member sizes.
        assert sp[1].decompressed_offset - sp[0].decompressed_offset == 3
        assert sp[2].decompressed_offset - sp[1].decompressed_offset == 2
        assert f._size - sp[2].decompressed_offset == 1  # type: ignore[operator]
        assert f._size == 6  # type: ignore[attr-defined]


def test_read_index_backwards():
    parts = [b"hello", b"world"]
    data = make_multi_member(parts)
    stream = io.BytesIO(data)
    members = _read_index_backwards(stream, len(data))
    assert len(members) == 2
    assert members[0].decompressed_start == 0
    assert members[0].decompressed_size == len(parts[0])
    assert members[1].decompressed_start == len(parts[0])
    assert members[1].decompressed_size == len(parts[1])
    assert (
        members[0].compressed_start + members[0].compressed_size
        == members[1].compressed_start
    )
    assert members[1].compressed_start + members[1].compressed_size == len(data)


# ---------------------------------------------------------------------------
# Corruption detection
# ---------------------------------------------------------------------------


def test_crc_corruption_in_first_member():
    parts = [b"good data", b"also good"]
    data = bytearray(make_multi_member(parts))
    # Flip a byte inside the LZMA stream of member 0 (after the 6-byte header)
    data[10] ^= 0xFF
    with open_lzip(bytes(data)) as f:
        with pytest.raises(ArchiveCorruptedError):
            f.read()


def test_crc_corruption_in_second_member():
    parts = [b"good data", b"corrupt this"]
    data = bytearray(make_multi_member(parts))
    # Corrupt the CRC32 field in member 1's trailer (last 20 bytes, first 4)
    trailer_start = len(data) - 20
    data[trailer_start] ^= 0xFF
    with open_lzip(bytes(data)) as f:
        with pytest.raises(ArchiveCorruptedError):
            f.read()


def test_truncation_mid_member():
    parts = [b"full member", b"truncated here"]
    data = make_multi_member(parts)
    truncated = data[: len(data) - 5]  # chop off last 5 bytes (inside trailer)
    with open_lzip(truncated) as f:
        with pytest.raises(ArchiveEOFError):
            f.read()


def test_invalid_member_size_in_trailer_falls_back_to_forward_decompression():
    """A member_size smaller than header+trailer causes the backwards scan to
    fall back gracefully (it's indistinguishable from trailing data at that
    layer).  The file content is still readable via sequential decompression
    because member_size is not used during forward decoding."""
    parts = [b"valid", b"also valid"]
    data = bytearray(make_multi_member(parts))
    # Corrupt only member_size (last 8 bytes); CRC and data_size are intact.
    struct.pack_into("<Q", data, len(data) - 8, 5)  # absurdly small member_size
    with open_lzip(bytes(data)) as f:
        f.seek(0, io.SEEK_END)  # must not raise
        f.seek(0)
        assert f.read() == b"".join(parts)


def test_trailing_data_falls_back_to_forward_decompression():
    """Trailing bytes after the last lzip member are valid per the lzip spec.
    The backwards scan should fail gracefully and fall back to sequential
    decompression rather than raising ArchiveCorruptedError."""
    parts = [b"hello", b"world"]
    data = make_multi_member(parts) + b"\x00" * 16  # 16 bytes of trailing data
    with open_lzip(data) as f:
        # SEEK_END must not raise, even though the backwards scan will fail.
        size = f.seek(0, io.SEEK_END)
        assert size == sum(len(p) for p in parts)
        f.seek(0)
        assert f.read() == b"helloworld"


def test_trailing_data_seek_falls_back_to_forward_decompression():
    """Same as above but triggered via a forward seek past the known frontier."""
    parts = [b"aaa" * 10, b"bbb" * 10]
    data = make_multi_member(parts) + b"\xff" * 8
    with open_lzip(data) as f:
        f.seek(len(parts[0]))  # triggers backwards scan attempt; should not raise
        assert f.read(len(parts[1])) == parts[1]


def test_wrong_member_size_backwards_scan_falls_back():
    """A plausible-but-wrong member_size causes the backwards scan to jump to
    an offset where LZIP magic is not present.  The scan falls back gracefully;
    sequential decompression succeeds because member_size is unused there."""
    parts = [b"first" * 10, b"second" * 10]
    data = bytearray(make_multi_member(parts))
    # Corrupt only member_size (off by 3); CRC and data_size are intact.
    struct.pack_into("<Q", data, len(data) - 8, len(data) - 3)
    with open_lzip(bytes(data)) as f:
        f.seek(0, io.SEEK_END)  # must not raise
        f.seek(0)
        assert f.read() == b"".join(parts)


# ---------------------------------------------------------------------------
# Issue 1: lzma.LZMAError must be translated to ArchiveCorruptedError
# ---------------------------------------------------------------------------


def test_translate_lzip_exception_wraps_lzma_error():
    """_translate_lzip_exception must map lzma.LZMAError → ArchiveCorruptedError
    so that raw stdlib exceptions never escape through the stream API."""
    result = _translate_lzip_exception(lzma.LZMAError("bad data"))
    assert isinstance(result, ArchiveCorruptedError)


# ---------------------------------------------------------------------------
# Issue 2: empty / non-lzip input must raise, not return empty bytes
# ---------------------------------------------------------------------------


def test_empty_file_raises_corrupted_error():
    """An empty byte stream is not a valid lzip file; at least one member required."""
    with open_lzip(b"") as f:
        with pytest.raises(ArchiveCorruptedError):
            f.read()


def test_non_lzip_data_raises_corrupted_error():
    """Data that doesn't start with the LZIP magic must raise ArchiveCorruptedError,
    not silently return an empty stream."""
    gzip_magic = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03"
    with open_lzip(gzip_magic) as f:
        with pytest.raises(ArchiveCorruptedError):
            f.read()


# ---------------------------------------------------------------------------
# Issue 3: coded_dict exponent must be validated (valid range: 12–29)
# ---------------------------------------------------------------------------


def _member_with_dict_exp(data: bytes, exp: int) -> bytes:
    """Build a lzip member whose header advertises the given dict exponent."""
    member = bytearray(create_lzip_member(data))
    member[5] = exp  # overwrite coded_dict byte; data/CRC unchanged
    return bytes(member)


def test_invalid_dict_exponent_too_small():
    """A coded_dict exponent below 12 (spec minimum) must raise ArchiveCorruptedError."""
    with open_lzip(_member_with_dict_exp(b"hello", exp=10)) as f:
        with pytest.raises(ArchiveCorruptedError):
            f.read()


def test_invalid_dict_exponent_too_large():
    """A coded_dict exponent above 29 (spec maximum) must raise ArchiveCorruptedError."""
    with open_lzip(_member_with_dict_exp(b"hello", exp=30)) as f:
        with pytest.raises(ArchiveCorruptedError):
            f.read()


def test_valid_dict_exponent_boundary_values():
    """Exponents 12 and 29 are the spec boundaries and must succeed."""
    with open_lzip(make_multi_member([b"hi"], dict_size_bits=12)) as f:
        assert f.read() == b"hi"
    with open_lzip(make_multi_member([b"hi"], dict_size_bits=29)) as f:
        assert f.read() == b"hi"
