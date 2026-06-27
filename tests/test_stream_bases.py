"""The shared read-only stream bases (`ReadOnlyIOStream`, `DelegatingStream`)."""

from __future__ import annotations

import io

import pytest

from archivey.internal.streams.streamtools import DelegatingStream, ReadOnlyIOStream


class _FixedReader(ReadOnlyIOStream):
    """Minimal subclass: only implements read(), to exercise the base's derived methods."""

    def __init__(self, data: bytes) -> None:
        super().__init__()
        self._buf = io.BytesIO(data)

    def read(self, n: int = -1, /) -> bytes:
        return self._buf.read(n)


def test_readonly_base_derives_readinto_readall_and_flags() -> None:
    s = _FixedReader(b"hello world")
    # readinto is derived from read()
    buf = bytearray(5)
    assert s.readinto(buf) == 5
    assert bytes(buf) == b"hello"
    # readall reads the rest via the read-loop
    assert s.readall() == b" world"
    assert s.readable() is True
    assert s.writable() is False
    with pytest.raises(io.UnsupportedOperation):
        s.write(b"x")


def test_readonly_base_read_is_abstract() -> None:
    with pytest.raises(NotImplementedError):
        ReadOnlyIOStream().read()


def test_delegating_base_forwards_to_inner() -> None:
    inner = io.BytesIO(b"abcdefgh")
    s = DelegatingStream(inner)
    assert s.read(3) == b"abc"
    assert s.tell() == 3
    assert s.seekable() is True
    assert s.seek(0) == 0
    assert s.read(2) == b"ab"
    # zero-copy readinto passthrough to the inner
    buf = bytearray(4)
    assert s.readinto(buf) == 4
    assert bytes(buf) == b"cdef"
    assert s.readable() is True and s.writable() is False


def test_delegating_base_close_closes_inner() -> None:
    inner = io.BytesIO(b"data")
    s = DelegatingStream(inner)
    s.close()
    assert inner.closed
    assert s.closed
    s.close()  # idempotent


def test_delegating_base_readinto_falls_back_without_inner_readinto() -> None:
    class _NoReadinto:
        def __init__(self, data: bytes) -> None:
            self._b = io.BytesIO(data)

        def read(self, n: int = -1, /) -> bytes:
            return self._b.read(n)

    s = DelegatingStream(_NoReadinto(b"xyz"))  # type: ignore[arg-type]
    buf = bytearray(2)
    assert s.readinto(buf) == 2
    assert bytes(buf) == b"xy"
