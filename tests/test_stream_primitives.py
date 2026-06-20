"""Unit tests for the stream primitives/helpers (``internal/streams``).

These are the shared building blocks every later format depends on, so they get focused
unit tests of their corner cases (per CONTRIBUTING's narrow exception for low-level
primitives).
"""

from __future__ import annotations

import io

import pytest

from archivey.internal.streams.binaryio import (
    BinaryIOWrapper,
    ensure_binaryio,
    ensure_bufferedio,
    is_filename,
    is_seekable,
    is_stream,
    read_exact,
)
from archivey.internal.streams.slice import SlicingStream, fix_stream_start_position
from tests.streams_util import CountingBytesIO, NonSeekableBytesIO

DATA = b"0123456789abcdefghijklmnopqrstuvwxyz"


# --- SlicingStream ---------------------------------------------------------------------


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


# --- read_exact ------------------------------------------------------------------------


def test_read_exact_full() -> None:
    assert read_exact(io.BytesIO(DATA), 10) == DATA[:10]


def test_read_exact_short_at_eof() -> None:
    assert read_exact(io.BytesIO(b"abc"), 10) == b"abc"


def test_read_exact_zero() -> None:
    assert read_exact(io.BytesIO(DATA), 0) == b""


def test_read_exact_negative_raises() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        read_exact(io.BytesIO(DATA), -1)


def test_read_exact_handles_short_reads() -> None:
    class _Drip(io.RawIOBase):
        def __init__(self, data: bytes) -> None:
            self._data = data
            self._pos = 0

        def readable(self) -> bool:
            return True

        def read(self, n: int = -1, /) -> bytes:
            # Always return at most 1 byte, to exercise the gather loop.
            if self._pos >= len(self._data):
                return b""
            chunk = self._data[self._pos : self._pos + 1]
            self._pos += 1
            return chunk

    assert read_exact(_Drip(DATA), 5) == DATA[:5]


# --- BinaryIOWrapper -------------------------------------------------------------------


class _ReadOnlyNoReadinto:
    """A minimal read-only object that lacks ``readinto`` (forces the fallback path)."""

    def __init__(self, data: bytes) -> None:
        self._inner = io.BytesIO(data)

    def read(self, n: int = -1) -> bytes:
        return self._inner.read(n)


def test_binaryiowrapper_read_and_readinto_fallback() -> None:
    wrapper = BinaryIOWrapper(_ReadOnlyNoReadinto(DATA))
    assert wrapper.read(5) == DATA[:5]
    buf = bytearray(4)
    assert wrapper.readinto(buf) == 4
    assert bytes(buf) == DATA[5:9]


def test_binaryiowrapper_readinto_uses_native_when_present() -> None:
    wrapper = BinaryIOWrapper(io.BytesIO(DATA))
    buf = bytearray(6)
    assert wrapper.readinto(buf) == 6
    assert bytes(buf) == DATA[:6]


def test_binaryiowrapper_read_to_eof() -> None:
    wrapper = BinaryIOWrapper(io.BytesIO(b"abc"))
    assert wrapper.read() == b"abc"
    assert wrapper.read() == b""


def test_binaryiowrapper_write_unsupported_on_readonly() -> None:
    wrapper = BinaryIOWrapper(_ReadOnlyNoReadinto(DATA))
    with pytest.raises(io.UnsupportedOperation):
        wrapper.write(b"x")


def test_binaryiowrapper_does_not_close_underlying() -> None:
    inner = io.BytesIO(DATA)
    wrapper = BinaryIOWrapper(inner)
    wrapper.close()
    assert not inner.closed  # wrapper must not close a stream it doesn't own


# --- is_seekable / is_stream / is_filename ---------------------------------------------


def test_is_seekable_true_false() -> None:
    assert is_seekable(io.BytesIO(b"x"))
    assert not is_seekable(NonSeekableBytesIO(b"x"))


def test_is_seekable_unwraps_buffered_reader() -> None:
    nonseek = NonSeekableBytesIO(DATA)
    buffered = io.BufferedReader(nonseek)
    assert not is_seekable(buffered)


def test_is_seekable_no_method() -> None:
    class _NoSeekable:
        def read(self, n: int = -1) -> bytes:
            return b""

    assert not is_seekable(_NoSeekable())


def test_is_stream_and_is_filename() -> None:
    assert is_stream(io.BytesIO(b"x"))
    assert not is_stream("path.zip")
    assert is_filename("path.zip")
    assert is_filename(b"path.zip")
    assert not is_filename(io.BytesIO(b"x"))


def test_is_stream_rejects_incomplete_object() -> None:
    class _Partial:
        def read(self, n: int = -1) -> bytes:
            return b""

    assert not is_stream(_Partial())


# --- ensure_binaryio / ensure_bufferedio -----------------------------------------------


def test_ensure_binaryio_passthrough() -> None:
    stream = io.BytesIO(DATA)
    assert ensure_binaryio(stream) is stream


def test_ensure_binaryio_wraps() -> None:
    wrapped = ensure_binaryio(_ReadOnlyNoReadinto(DATA))
    assert isinstance(wrapped, BinaryIOWrapper)
    assert wrapped.read(3) == DATA[:3]


def test_ensure_bufferedio_passthrough_for_buffered() -> None:
    inner = io.BytesIO(DATA)  # BytesIO is already a BufferedIOBase
    assert ensure_bufferedio(inner) is inner


def test_ensure_bufferedio_does_not_close_raw_source() -> None:
    inner = CountingBytesIO(DATA)  # a RawIOBase, so it gets wrapped in a buffer
    buffered = ensure_bufferedio(inner)
    assert buffered.read(4) == DATA[:4]
    buffered.close()
    assert not inner.closed  # the non-closing buffer detaches rather than closing


# --- fix_stream_start_position ---------------------------------------------------------


def test_fix_stream_start_position_at_zero_returns_same() -> None:
    stream = io.BytesIO(DATA)
    assert fix_stream_start_position(stream) is stream


def test_fix_stream_start_position_midstream_slices() -> None:
    stream = io.BytesIO(DATA)
    stream.seek(10)
    fixed = fix_stream_start_position(stream)
    assert fixed is not stream
    assert fixed.tell() == 0
    assert fixed.read(5) == DATA[10:15]


def test_fix_stream_start_position_non_seekable_passthrough() -> None:
    stream = NonSeekableBytesIO(DATA)
    assert fix_stream_start_position(stream) is stream
