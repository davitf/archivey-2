"""Unit tests for ``SlicingStream`` and ``fix_stream_start_position`` (``streams/slice.py``).

These are low-level building blocks every container backend relies on, so they get focused
corner-case coverage (per CONTRIBUTING's narrow exception for stream primitives).
"""

from __future__ import annotations

import io

import pytest

from archivey.internal.streams.slice import SlicingStream, fix_stream_start_position
from tests.streams_util import NonSeekableBytesIO

DATA = b"0123456789abcdefghijklmnopqrstuvwxyz"


class TestSlicingStream:
    def test_read_with_start_and_length(self) -> None:
        sliced = SlicingStream(io.BytesIO(DATA), start=5, length=10)
        assert sliced.read(3) == b"567"
        assert sliced.tell() == 3
        assert sliced.read() == b"89abcde"
        assert sliced.tell() == 10
        assert sliced.read(5) == b""

    def test_read_start_only_reads_to_end(self) -> None:
        sliced = SlicingStream(io.BytesIO(DATA), start=10)
        assert sliced.read() == DATA[10:]

    def test_read_length_only_from_current_position(self) -> None:
        underlying = io.BytesIO(DATA)
        underlying.seek(7)
        sliced = SlicingStream(underlying, length=10)
        assert sliced.read() == DATA[7:17]
        assert sliced.tell() == 10

    def test_read_spanning_then_clamped_at_length(self) -> None:
        sliced = SlicingStream(io.BytesIO(DATA), start=0, length=20)
        assert sliced.read(8) == DATA[:8]
        assert sliced.read(100) == DATA[8:20]  # clamped to the slice end
        assert sliced.read(1) == b""

    def test_read_zero_returns_empty(self) -> None:
        sliced = SlicingStream(io.BytesIO(DATA), start=0, length=10)
        assert sliced.read(0) == b""
        assert sliced.tell() == 0

    def test_empty_slice(self) -> None:
        sliced = SlicingStream(io.BytesIO(DATA), start=5, length=0)
        assert sliced.read() == b""
        assert sliced.read(10) == b""

    def test_slice_larger_than_underlying(self) -> None:
        sliced = SlicingStream(io.BytesIO(DATA[:10]), start=0, length=20)
        assert sliced.read() == DATA[:10]
        assert sliced.read(5) == b""

    def test_readinto(self) -> None:
        sliced = SlicingStream(io.BytesIO(DATA), start=5, length=10)
        buf = bytearray(4)
        n = sliced.readinto(buf)
        assert n == 4
        assert bytes(buf) == DATA[5:9]

    def test_seek_set_cur_end(self) -> None:
        sliced = SlicingStream(io.BytesIO(DATA), start=10, length=10)
        assert sliced.seek(3) == 3
        assert sliced.read(2) == DATA[13:15]
        assert sliced.seek(-2, io.SEEK_CUR) == 3
        assert sliced.read(4) == DATA[13:17]
        assert sliced.seek(-1, io.SEEK_END) == 9
        assert sliced.read(5) == DATA[19:20]

    def test_seek_past_end_then_empty_read(self) -> None:
        sliced = SlicingStream(io.BytesIO(DATA), start=10, length=10)
        assert sliced.seek(100) == 100
        assert sliced.read(1) == b""

    def test_seek_negative_raises(self) -> None:
        sliced = SlicingStream(io.BytesIO(DATA), start=10, length=10)
        with pytest.raises(ValueError, match="Negative seek position"):
            sliced.seek(-5)

    def test_seek_end_no_length_zero_offset(self) -> None:
        sliced = SlicingStream(io.BytesIO(DATA), start=10)
        assert sliced.seek(0, io.SEEK_END) == len(DATA) - 10
        assert sliced.read(1) == b""

    def test_seek_end_no_length_nonzero_offset_unsupported(self) -> None:
        sliced = SlicingStream(io.BytesIO(DATA), start=5)
        with pytest.raises(io.UnsupportedOperation, match="SEEK_END is not supported"):
            sliced.seek(-1, io.SEEK_END)

    def test_non_seekable_no_start(self) -> None:
        sliced = SlicingStream(NonSeekableBytesIO(DATA), length=15)
        assert sliced.read(5) == DATA[:5]
        assert sliced.read() == DATA[5:15]
        assert not sliced.seekable()

    def test_non_seekable_with_start_rejected(self) -> None:
        with pytest.raises(ValueError, match="Cannot slice a non-seekable stream"):
            SlicingStream(NonSeekableBytesIO(DATA), start=5, length=10)

    def test_seek_on_non_seekable_raises(self) -> None:
        sliced = SlicingStream(NonSeekableBytesIO(DATA), length=10)
        with pytest.raises(io.UnsupportedOperation, match="seek on non-seekable"):
            sliced.seek(5)


class TestFixStreamStartPosition:
    def test_at_zero_returns_same(self) -> None:
        stream = io.BytesIO(DATA)
        assert fix_stream_start_position(stream) is stream

    def test_midstream_slices(self) -> None:
        stream = io.BytesIO(DATA)
        stream.seek(10)
        fixed = fix_stream_start_position(stream)
        assert fixed is not stream
        assert fixed.tell() == 0
        assert fixed.read(5) == DATA[10:15]

    def test_non_seekable_passthrough(self) -> None:
        stream = NonSeekableBytesIO(DATA)
        assert fix_stream_start_position(stream) is stream
