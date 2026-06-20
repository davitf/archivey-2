"""Unit tests for ``streams/binaryio.py``: the classify/coerce helpers and BinaryIOWrapper.

These adapt arbitrary caller objects to a uniform ``BinaryIO``, so they are tested hard as
units (per CONTRIBUTING's narrow exception for stream primitives). The cross-library
"does every real stream type survive these helpers" matrix lives in ``test_stream_inputs``.
"""

from __future__ import annotations

import io
import os
import tempfile

import pytest

from archivey.internal.streams.binaryio import (
    BinaryIOWrapper,
    ReadableStream,
    ensure_binaryio,
    ensure_bufferedio,
    is_filename,
    is_seekable,
    is_stream,
    read_exact,
)
from tests.streams_util import CountingBytesIO, NonSeekableBytesIO

DATA = b"0123456789abcdefghijklmnopqrstuvwxyz"


class OnlyReadStream:
    """A bare read-only object: ``read()`` and nothing else (the canonical wrap target)."""

    def __init__(self, data: bytes) -> None:
        self._inner = io.BytesIO(data)

    def read(self, size: int = -1) -> bytes:
        return self._inner.read(size)


class ReadIntoStream(OnlyReadStream):
    """A partial file-like that *does* implement ``readinto`` (no io.IOBase base)."""

    def readinto(self, b) -> int:  # type: ignore[no-untyped-def]  # test double
        return self._inner.readinto(b)


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


def test_read_exact_gathers_across_short_reads() -> None:
    class _Drip(io.RawIOBase):
        def __init__(self, data: bytes) -> None:
            self._data = data
            self._pos = 0

        def readable(self) -> bool:
            return True

        def read(self, n: int = -1, /) -> bytes:
            # Return at most 1 byte per call, to exercise the gather loop.
            if self._pos >= len(self._data):
                return b""
            chunk = self._data[self._pos : self._pos + 1]
            self._pos += 1
            return chunk

    assert read_exact(_Drip(DATA), 5) == DATA[:5]


def test_read_exact_accepts_readablestream_protocol() -> None:
    assert isinstance(OnlyReadStream(DATA), ReadableStream)


# --- is_filename / is_stream / is_seekable ---------------------------------------------


def test_is_filename() -> None:
    assert is_filename("path.zip")
    assert is_filename(b"path.zip")
    assert is_filename(os.fspath("/tmp/x"))
    assert not is_filename(io.BytesIO(b"x"))
    assert not is_filename(OnlyReadStream(b"x"))


def test_is_stream_accepts_iobase() -> None:
    assert is_stream(io.BytesIO(b"x"))
    assert not is_stream("path.zip")


def test_is_stream_rejects_partial_object() -> None:
    # Has read() but is missing the rest of the BinaryIO surface (and isn't io.IOBase).
    assert not is_stream(OnlyReadStream(b"x"))


def test_is_stream_accepts_full_duck_typed_object() -> None:
    # Not an io.IOBase, but exposes the whole interface is_stream() checks for.
    class _Full:
        def read(self, n=-1):  # type: ignore[no-untyped-def]
            return b""

        def seek(self, o, w=0):  # type: ignore[no-untyped-def]
            return 0

        def tell(self):  # type: ignore[no-untyped-def]
            return 0

        def close(self):  # type: ignore[no-untyped-def]
            return None

        def readable(self):  # type: ignore[no-untyped-def]
            return True

        def writable(self):  # type: ignore[no-untyped-def]
            return False

        def seekable(self):  # type: ignore[no-untyped-def]
            return True

        def readinto(self, b):  # type: ignore[no-untyped-def]
            return 0

        closed = False

    assert is_stream(_Full())


def test_is_seekable_true_false() -> None:
    assert is_seekable(io.BytesIO(b"x"))
    assert not is_seekable(NonSeekableBytesIO(b"x"))


def test_is_seekable_unwraps_buffered_reader() -> None:
    # A BufferedReader reports seekable()=True even over a non-seekable raw stream.
    buffered = io.BufferedReader(NonSeekableBytesIO(DATA))
    assert not is_seekable(buffered)


def test_is_seekable_object_without_seekable_method() -> None:
    assert not is_seekable(OnlyReadStream(b"x"))


# --- BinaryIOWrapper -------------------------------------------------------------------


def test_wrapper_read_and_readinto_fallback() -> None:
    # OnlyReadStream has no readinto, so readinto() must fall back to read().
    wrapper = BinaryIOWrapper(OnlyReadStream(DATA))
    assert wrapper.read(5) == DATA[:5]
    buf = bytearray(4)
    assert wrapper.readinto(buf) == 4
    assert bytes(buf) == DATA[5:9]


def test_wrapper_readinto_uses_native_when_present() -> None:
    wrapper = BinaryIOWrapper(ReadIntoStream(DATA))
    buf = bytearray(6)
    assert wrapper.readinto(buf) == 6
    assert bytes(buf) == DATA[:6]


def test_wrapper_read_to_eof() -> None:
    wrapper = BinaryIOWrapper(OnlyReadStream(b"abc"))
    assert wrapper.read() == b"abc"
    assert wrapper.read() == b""  # genuine EOF stays b"", not an error


def test_wrapper_read_none_raises_blocking_not_eof() -> None:
    """A non-blocking read() returning None must not be reported as EOF (data loss)."""

    class _NonBlocking:
        def read(self, n: int = -1) -> bytes | None:
            return None  # "no data available right now", not EOF

    with pytest.raises(BlockingIOError):
        BinaryIOWrapper(_NonBlocking()).read(10)


def test_wrapper_readinto_none_raises_blocking() -> None:
    class _NonBlockingReadinto:
        def read(self, n: int = -1) -> bytes:
            return b""

        def readinto(self, b) -> int | None:  # type: ignore[no-untyped-def]
            return None

    with pytest.raises(BlockingIOError):
        BinaryIOWrapper(_NonBlockingReadinto()).readinto(bytearray(10))


def test_wrapper_readinto_falls_back_when_native_raises() -> None:
    class _BadReadinto(OnlyReadStream):
        def readinto(self, b):  # type: ignore[no-untyped-def]
            raise io.UnsupportedOperation("readinto")

    wrapper = BinaryIOWrapper(_BadReadinto(DATA))
    buf = bytearray(5)
    assert wrapper.readinto(buf) == 5
    assert bytes(buf) == DATA[:5]


def test_wrapper_writable_trusts_raw_writable_not_hasattr_write() -> None:
    """A read-only file *has* a write() method (it raises); writable() is the truth."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "f.bin")
        with open(path, "wb") as f:
            f.write(DATA)
        # A read-only file object: it exposes write() but writable() is False.
        raw = open(path, "rb", buffering=0)
        assert hasattr(raw, "write")  # the trap the old hasattr() check fell into
        wrapper = BinaryIOWrapper(raw)
        assert wrapper.writable() is False
        assert wrapper.readable() is True
        raw.close()


def test_wrapper_writable_for_object_without_writable_method() -> None:
    # OnlyReadStream has neither writable() nor write() -> not writable.
    assert BinaryIOWrapper(OnlyReadStream(DATA)).writable() is False

    class _Writer(OnlyReadStream):
        def write(self, data):  # type: ignore[no-untyped-def]
            return len(data)

    # Has write() but no writable() -> falls back to hasattr(write) -> True.
    assert BinaryIOWrapper(_Writer(DATA)).writable() is True


def test_wrapper_write_unsupported_on_readonly() -> None:
    wrapper = BinaryIOWrapper(OnlyReadStream(DATA))
    with pytest.raises(io.UnsupportedOperation):
        wrapper.write(b"x")


def test_wrapper_seek_tell_unsupported_when_absent() -> None:
    wrapper = BinaryIOWrapper(OnlyReadStream(DATA))
    assert wrapper.seekable() is False
    with pytest.raises(io.UnsupportedOperation):
        wrapper.seek(0)
    with pytest.raises(io.UnsupportedOperation):
        wrapper.tell()


def test_wrapper_seek_tell_delegate_when_present() -> None:
    wrapper = BinaryIOWrapper(io.BytesIO(DATA))
    assert wrapper.seekable() is True
    assert wrapper.read(5) == DATA[:5]
    assert wrapper.tell() == 5
    assert wrapper.seek(0) == 0
    assert wrapper.read(3) == DATA[:3]


def test_wrapper_does_not_close_underlying() -> None:
    inner = io.BytesIO(DATA)
    wrapper = BinaryIOWrapper(inner)
    wrapper.close()
    assert wrapper.closed
    assert not inner.closed  # must not close a stream it doesn't own


# --- ensure_binaryio -------------------------------------------------------------------


def test_ensure_binaryio_passthrough() -> None:
    stream = io.BytesIO(DATA)
    assert ensure_binaryio(stream) is stream


def test_ensure_binaryio_wraps_partial_object() -> None:
    wrapped = ensure_binaryio(OnlyReadStream(DATA))
    assert isinstance(wrapped, BinaryIOWrapper)
    assert wrapped.read(3) == DATA[:3]
    assert wrapped.readable() is True
    assert wrapped.writable() is False
    assert wrapped.seekable() is False


# --- ensure_bufferedio -----------------------------------------------------------------


def test_ensure_bufferedio_passthrough_for_buffered() -> None:
    inner = io.BytesIO(DATA)  # already a BufferedIOBase
    assert ensure_bufferedio(inner) is inner


def test_ensure_bufferedio_wraps_rawiobase() -> None:
    inner = CountingBytesIO(DATA)  # a RawIOBase
    buffered = ensure_bufferedio(inner)
    assert isinstance(buffered, io.BufferedReader)
    assert buffered.read(4) == DATA[:4]


def test_ensure_bufferedio_wraps_non_iobase_object() -> None:
    # A stream-like that is not an io.IOBase: BufferedReader would reject it directly, so
    # ensure_bufferedio must adapt it through BinaryIOWrapper first.
    buffered = ensure_bufferedio(OnlyReadStream(DATA))
    assert buffered.read() == DATA


def test_ensure_bufferedio_does_not_close_raw_source() -> None:
    inner = CountingBytesIO(DATA)
    buffered = ensure_bufferedio(inner)
    assert buffered.read(4) == DATA[:4]
    buffered.close()
    assert not inner.closed  # the non-closing buffer detaches rather than closing
