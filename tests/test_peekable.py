"""Tests for the ``PeekableStream`` primitive (Phase 3, §1)."""

from __future__ import annotations

import io

from archivey.internal.streams.peekable import DETECTION_LIMIT, PeekableStream
from tests.streams_util import NonSeekableBytesIO


def test_peek_does_not_consume() -> None:
    stream = PeekableStream(NonSeekableBytesIO(b"0123456789"))
    assert stream.peek(4) == b"0123"
    # A second peek sees the same bytes; nothing was consumed.
    assert stream.peek(4) == b"0123"
    assert stream.tell() == 0


def test_read_replays_buffer_then_passes_through() -> None:
    stream = PeekableStream(NonSeekableBytesIO(b"abcdefghij"))
    stream.peek(4)  # buffer "abcd"
    # First read drains the peeked buffer...
    assert stream.read(2) == b"ab"
    assert stream.read(2) == b"cd"
    # ...then falls through to the underlying stream with no bytes dropped.
    assert stream.read(6) == b"efghij"
    assert stream.read(1) == b""
    assert stream.tell() == 10


def test_read_all_drains_buffer_and_underlying() -> None:
    stream = PeekableStream(NonSeekableBytesIO(b"hello world"))
    stream.peek(5)
    assert stream.read() == b"hello world"
    assert stream.read() == b""


def test_peek_beyond_buffered_limit_grows() -> None:
    # Peeking more than the default window grows the buffer on demand (the ISO probe needs
    # 32 774 bytes); the same bytes are then still replayed on read.
    data = bytes(range(256)) * 200  # 51 200 bytes, > DETECTION_LIMIT and > the ISO window
    assert len(data) > DETECTION_LIMIT
    stream = PeekableStream(NonSeekableBytesIO(data))
    big = stream.peek(32774)
    assert big == data[:32774]
    assert stream.read(len(data)) == data


def test_peek_past_eof_returns_short() -> None:
    stream = PeekableStream(NonSeekableBytesIO(b"abc"))
    assert stream.peek(100) == b"abc"
    assert stream.read() == b"abc"


def test_readinto_replays_buffer() -> None:
    stream = PeekableStream(NonSeekableBytesIO(b"abcdef"))
    stream.peek(3)
    buf = bytearray(4)
    n = stream.readinto(buf)
    assert n == 4
    assert bytes(buf) == b"abcd"


def test_reports_non_seekable() -> None:
    stream = PeekableStream(NonSeekableBytesIO(b"x"))
    assert stream.seekable() is False
    assert stream.readable() is True


def test_close_does_not_close_underlying() -> None:
    underlying = NonSeekableBytesIO(b"data")
    stream = PeekableStream(underlying)
    stream.close()
    # The wrapper is closed, but the caller-owned underlying stream is left usable.
    assert underlying.closed is False
    assert underlying.read() == b"data"


def test_name_passthrough(tmp_path) -> None:
    path = tmp_path / "thing.bin"
    path.write_bytes(b"abc")
    with open(path, "rb") as f:
        stream = PeekableStream(f)
        assert stream.name == str(path)


def test_name_absent_for_anonymous_stream() -> None:
    # Same contract as SlicingStream: no name attr when the underlying has none.
    stream = PeekableStream(NonSeekableBytesIO(b"abc"))
    assert not hasattr(stream, "name")


def test_works_as_binaryio_for_buffered_reader() -> None:
    # The backend may wrap a PeekableStream in a BufferedReader; the prefix must survive.
    stream = PeekableStream(NonSeekableBytesIO(b"0123456789"))
    stream.peek(4)
    reader = io.BufferedReader(stream)
    assert reader.read(10) == b"0123456789"
