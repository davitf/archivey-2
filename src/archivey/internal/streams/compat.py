"""Type/seek compatibility shims, stream wrappers, and lazy file opening."""

import io
import logging
import os
from contextlib import contextmanager
from typing import (
    IO,
    Any,
    BinaryIO,
    Iterator,
    Protocol,
    TypeGuard,
    Union,
    runtime_checkable,
)

from archivey.types import ReadableBinaryStream as ReadableBinaryStream
from archivey.types import ReadableStreamLikeOrSimilar as ReadableStreamLikeOrSimilar

logger = logging.getLogger(__name__)


# ReadableBinaryStream and ReadableStreamLikeOrSimilar are now in archivey.types
# WritableBinaryStream and CloseableStream remain here as they are not part of the circular import.
@runtime_checkable
class WritableBinaryStream(Protocol):
    def write(self, data: bytes, /) -> int: ...


@runtime_checkable
class CloseableStream(Protocol):
    def close(self) -> None: ...


BinaryStreamLike = Union[ReadableBinaryStream, WritableBinaryStream]
# ReadableStreamLikeOrSimilar is imported from archivey.types


def read_exact(
    stream: ReadableBinaryStream, n: int
) -> bytes:  # Uses ReadableBinaryStream from types
    """Read exactly ``n`` bytes, or all available bytes if the file ends."""

    if n < 0:
        raise ValueError("n must be non-negative")

    data = bytearray()
    while len(data) < n:
        chunk = stream.read(n - len(data))
        if not chunk:
            break
        data.extend(chunk)
    return bytes(data)


def is_seekable(
    stream: io.IOBase | IO[bytes] | BinaryStreamLike,
) -> bool:
    """Check if a stream is seekable."""
    # When we wrap a RewindableNonSeekableStream in a BufferedReader, we want to check
    # if the inner stream is seekable, with the check below.
    if isinstance(stream, io.BufferedReader):
        return is_seekable(stream.raw)

    try:
        return stream.seekable() or False  # type: ignore[union-attr]
    except AttributeError as e:
        # Some streams (e.g. tarfile._Stream) don't have a seekable method, which seems
        # like a bug. Sometimes they are wrapped in other classes
        # (e.g. tarfile._FileInFile) that do have one and assume the inner ones also do.
        #
        # In the tarfile case specifically, _Stream actually does have a seek() method,
        # but calling seek() on the stream returned by tarfile will raise an exception,
        # as it's wrapped in a BufferedReader which calls seekable() when doing a
        # seek().
        logger.debug("Stream %s does not have a seekable method: %s", stream, e)
        return False


class BinaryIOWrapper(io.RawIOBase, BinaryIO):
    """
    Wraps an object that doesn't match the BinaryIO protocol, adding any missing
    methods to make the type checker happy.
    """

    def __init__(self, raw: BinaryStreamLike):
        self._raw = raw

    def read(self, size: int = -1, /) -> bytes | None:  # type: ignore[override]
        if hasattr(self._raw, "read"):
            return self._raw.read(size)

        return super().read(size)

    def write(self, data: bytes, /) -> int:  # type: ignore[override]
        if not hasattr(self._raw, "write"):
            raise io.UnsupportedOperation("write not supported")
        return self._raw.write(data)

    def _readinto_from_read(self, b: bytearray | memoryview, /) -> int | None:
        data = self.read(len(b))
        if data is None:
            return None
        b[: len(data)] = data
        return len(data)

    def readinto(self, b: bytearray | memoryview, /) -> int | None:  # type: ignore[override]
        if not hasattr(self._raw, "readinto"):
            return self._readinto_from_read(b)

        try:
            bytes_read: int | None = self._raw.readinto(b)
            return bytes_read
        except (NotImplementedError, io.UnsupportedOperation):
            # Some streams don't support readinto, so we fall back to read()
            return self._readinto_from_read(b)

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        if hasattr(self._raw, "seek"):
            pos: int = self._raw.seek(offset, whence)
            return pos

        raise io.UnsupportedOperation("seek")

    def tell(self, /) -> int:
        if hasattr(self._raw, "tell"):
            pos: int = self._raw.tell()
            return pos
        raise io.UnsupportedOperation("tell")

    def close(self) -> None:
        super().close()
        # Don't close the underlying stream, as this may be a temporary wrapper.

    def flush(self) -> None:
        if (
            hasattr(self._raw, "flush")
            and hasattr(self._raw, "closed")
            and not self._raw.closed
        ):
            return self._raw.flush()  # type: ignore[no-any-return]
        return None

    def readable(self) -> bool:
        try:
            result: bool | None = self._raw.readable()  # type: ignore[union-attr]
            # The result can be None if the class just extended BinaryIO and didn't
            # actually implement the method.
            if result is not None:
                return result

        except AttributeError:
            pass

        return hasattr(self._raw, "read") or hasattr(self._raw, "readinto")

    def writable(self) -> bool:  # type: ignore[return]
        try:
            result: bool | None = self._raw.writable()  # type: ignore[union-attr]
            # The result can be None if the class just extended BinaryIO and didn't
            # actually implement the method.
            if result is not None:
                return result

        except AttributeError:
            return hasattr(self._raw, "write")

    def seekable(self) -> bool:
        return is_seekable(self._raw)

    def __str__(self) -> str:
        return f"BinaryIOWrapper({self._raw!s})"

    def __repr__(self) -> str:
        return f"BinaryIOWrapper({self._raw!r})"


ALL_IO_METHODS = {
    "read",
    "write",
    "seek",
    "tell",
    "__enter__",
    "__exit__",
    "close",
    "flush",
    "readable",
    "writable",
    "seekable",
    "readline",
    "readlines",
    "readinto",
    "write",
    "writelines",
}

ALL_IO_PROPERTIES = {
    "closed",
}


def is_filename(obj: Any) -> TypeGuard[str | bytes | os.PathLike[str]]:
    """Check if an object is a filename-like object."""
    return isinstance(obj, (str, bytes, os.PathLike))


def is_stream(obj: Any) -> TypeGuard[BinaryIO]:
    """Check if an object matches the BinaryIO protocol."""

    # First check if it's a standard IOBase instance
    is_iobase = isinstance(obj, io.IOBase)

    missing_methods = {m for m in ALL_IO_METHODS if not callable(getattr(obj, m, None))}
    missing_properties = {p for p in ALL_IO_PROPERTIES if not hasattr(obj, p)}
    has_all_interface = not missing_methods and not missing_properties

    if not isinstance(obj, (str, bytes, os.PathLike)) and not has_all_interface:
        logger.debug(
            "Object %r does not match the BinaryIO protocol: missing methods %r, "
            "missing properties %r",
            obj,
            missing_methods,
            missing_properties,
        )

    if is_iobase != has_all_interface:
        logger.debug(
            "Object %r : is_iobase=%r, has_all_interface=%r",
            obj,
            is_iobase,
            has_all_interface,
        )

    return is_iobase or has_all_interface


def ensure_binaryio(obj: BinaryStreamLike) -> BinaryIO:
    """Some libraries return an object that doesn't match the BinaryIO protocol,
    so we need to ensure it does to make the type checker happy."""

    if is_stream(obj):
        return obj

    logger.debug(
        "Object %r does not match the BinaryIO protocol, wrapping in BinaryIOWrapper.",
        obj,
    )
    return BinaryIOWrapper(obj)


class NonClosingBufferedReader(io.BufferedReader):
    def close(self) -> None:
        self.detach()
        # The BufferedReader raises a ValueError if we call super().close() here after
        # detach() has been called.
        # super().close()


def ensure_bufferedio(obj: BinaryStreamLike) -> io.BufferedIOBase:
    if isinstance(obj, io.BufferedIOBase):
        return obj

    if not isinstance(obj, io.RawIOBase):
        # BufferedReader requires the underlying stream to be a RawIOBase.
        obj = BinaryIOWrapper(obj)

    # BufferedReader closes the underlying stream when closed or deleted. If
    # ensure_bufferedio is called to temporarily buffer a stream (e.g. when opening
    # a compressed stream), we need to ensure that the underlying stream is not closed
    # when the BufferedReader is closed or goes out of scope. The underlying stream
    # will be closed when it's garbage collected anyway, so we don't need to worry
    # about it leaking.
    return NonClosingBufferedReader(obj)


def fix_stream_start_position(stream: BinaryIO) -> BinaryIO:
    from archivey.internal.streams.slice import SlicingStream

    if not is_seekable(stream):
        return stream
    start_pos = stream.tell()
    if start_pos == 0:
        return stream

    return SlicingStream(stream, start=start_pos)


@contextmanager
def open_if_file(
    path_or_stream: str | bytes | os.PathLike[str] | ReadableStreamLikeOrSimilar,
    rewind: bool = True,
) -> Iterator[BinaryIO]:
    if is_stream(path_or_stream):
        if rewind:
            # Using an assert here, as this should never be called with a non-seekable stream.
            assert is_seekable(path_or_stream)
            initial_pos = path_or_stream.tell()

        yield ensure_binaryio(path_or_stream)
        if rewind:
            path_or_stream.seek(initial_pos)

    elif is_filename(path_or_stream):
        with open(path_or_stream, "rb") as f:
            yield f
    else:
        raise ValueError(f"Expected a filename or stream, got {type(path_or_stream)}")
