"""Provides I/O helper classes, including exception translation and lazy opening."""

import io
import logging
import os
from contextlib import contextmanager  # Added for open_if_file
from dataclasses import dataclass, field
from typing import (
    IO,
    Any,
    BinaryIO,
    Callable,
    Iterator,
    Optional,
    Protocol,
    TypeGuard,
    TypeVar,
    Union,
    runtime_checkable,
)

from archivey.exceptions import ArchiveError
from archivey.internal.utils import ensure_not_none
from archivey.types import ReadableBinaryStream, ReadableStreamLikeOrSimilar

logger = logging.getLogger(__name__)


ExceptionTranslatorFn = Callable[[Exception], Optional[ArchiveError]]


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
            data = self._raw.read(size)
            # If read succeeded, we can use it directly for future reads
            self.read = self._raw.read  # type: ignore[method-assign]
            return data

        return super().read(size)

    def write(self, data: bytes, /) -> int:  # type: ignore[override]
        if not hasattr(self._raw, "write"):
            raise io.UnsupportedOperation("write not supported")
        self.write = self._raw.write  # type: ignore[method-assign]
        return self._raw.write(data)

    def _readinto_from_read(self, b: bytearray | memoryview, /) -> int | None:
        data = self.read(len(b))
        if data is None:
            return None
        b[: len(data)] = data
        return len(data)

    def readinto(self, b: bytearray | memoryview, /) -> int | None:  # type: ignore[override]
        if not hasattr(self._raw, "readinto"):
            self.readinto = self._readinto_from_read  # type: ignore[method-assign]
            return self._readinto_from_read(b)

        try:
            bytes_read: int | None = self._raw.readinto(b)
            # If readinto succeeded, we can use it for future reads
            self.readinto = self._raw.readinto  # type: ignore[method-assign]
            return bytes_read
        except (NotImplementedError, io.UnsupportedOperation):
            # Some streams don't support readinto, so we fall back to read()
            self.readinto = self._readinto_from_read  # type: ignore[method-assign]
            return self._readinto_from_read(b)

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        if hasattr(self._raw, "seek"):
            pos: int = self._raw.seek(offset, whence)
            # If seek succeeded, we can use it for future seeks
            self.seek = self._raw.seek  # type: ignore[method-assign]
            return pos

        raise io.UnsupportedOperation("seek")

    def tell(self, /) -> int:
        if hasattr(self._raw, "tell"):
            pos: int = self._raw.tell()
            # If tell succeeded, we can use it for future tells
            self.tell = self._raw.tell  # type: ignore[method-assign]
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


class ErrorIOStream(io.RawIOBase, BinaryIO):
    """
    An I/O stream that always raises a predefined exception on any I/O operation.

    This is useful for testing error handling paths or for representing
    unreadable members within an archive without returning None.
    """

    def __init__(self, exc: Exception):
        """Initialize the error stream."""
        self._exc = exc

    def read(self, size: int = -1) -> bytes:
        """Raise the stored exception."""
        raise self._exc

    def write(self, b: bytes) -> int:  # type: ignore[override]
        """Raise the stored exception."""
        raise self._exc

    def readable(self) -> bool:
        return True  # pragma: no cover - trivial

    def writable(self) -> bool:
        return False  # pragma: no cover - trivial

    def seekable(self) -> bool:
        return False  # pragma: no cover - trivial


T = TypeVar("T")


def run_with_exception_translation(
    func: Callable[[], T],
    exception_translator: Callable[[Exception], Optional[ArchiveError]],
    archive_path: str | None = None,
    member_name: str | None = None,
) -> T:
    try:
        return func()
    except ArchiveError as e:
        if archive_path is not None:
            e.archive_path = archive_path
        if member_name is not None:
            e.member_name = member_name
        raise e

    except Exception as e:
        translated = exception_translator(e)
        if translated is not None:
            translated.archive_path = archive_path
            translated.member_name = member_name
            logger.debug(
                "Translated exception: %r -> %r",
                e,
                translated,
            )
            raise translated from e
        raise e


@dataclass
class IOStats:
    """Simple container for I/O statistics."""

    bytes_read: int = 0
    seek_calls: int = 0
    read_ranges: list[list[int]] = field(default_factory=lambda: [[0, 0]])


class StatsIO(io.RawIOBase, BinaryIO):
    """
    An I/O stream wrapper that tracks statistics about read and seek operations
    performed on an underlying stream.

    This can be useful for debugging, performance analysis, or understanding
    access patterns.
    """

    def __init__(self, inner: IO[bytes], stats: IOStats) -> None:
        super().__init__()
        self._inner = inner
        self.stats = stats

    # Basic IO methods -------------------------------------------------
    def read(self, n: int = -1) -> bytes:
        data = self._inner.read(n)
        self.stats.bytes_read += len(data)
        self.stats.read_ranges[-1][1] += len(data)
        return data

    def readinto(self, b: bytearray | memoryview) -> int:  # type: ignore[override]
        try:
            n: int = self._inner.readinto(b)  # type: ignore[attr-defined]
            self.stats.bytes_read += n
            self.stats.read_ranges[-1][1] += n
            return n
        except NotImplementedError:
            # Some streams don't support readinto, so we fall back to read()
            data = self.read(len(b))
            b[: len(data)] = data
            self.stats.bytes_read += len(data)
            self.stats.read_ranges[-1][1] += len(data)
            return len(data)

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        newpos = self._inner.seek(offset, whence)
        if offset != 0 or whence != 1:
            # Called by IOBase.tell(), doesn't actually move the stream. Ignore these seeks.
            self.stats.seek_calls += 1
            self.stats.read_ranges.append([newpos, 0])

        return newpos

    def readable(self) -> bool:  # pragma: no cover - trivial
        return self._inner.readable()

    def writable(self) -> bool:  # pragma: no cover - trivial
        return self._inner.writable()

    def seekable(self) -> bool:  # pragma: no cover - trivial
        return self._inner.seekable()

    def write(self, b: Any) -> int:  # pragma: no cover - simple delegation
        return self._inner.write(b)

    def close(self) -> None:  # pragma: no cover - simple delegation
        self._inner.close()
        super().close()

    # Delegate unknown attributes --------------------------------------
    def __getattr__(self, item: str) -> Any:  # pragma: no cover - simple
        return getattr(self._inner, item)


class ConcatenationStream(io.RawIOBase, BinaryIO):
    """Concatenate multiple streams sequentially."""

    _streams: list[ReadableStreamLikeOrSimilar]
    _index: int

    def __init__(self, streams: list[ReadableStreamLikeOrSimilar]):
        super().__init__()

        # Flatten multiple concatenation streams to avoid extra overhead.
        flattened_streams: list[ReadableStreamLikeOrSimilar] = []

        for stream in streams:
            if isinstance(stream, ConcatenationStream):
                flattened_streams.extend(stream._streams[stream._index :])
            else:
                flattened_streams.append(stream)

        self._streams = flattened_streams
        self._index = 0

    # Basic IO methods -------------------------------------------------
    def read(self, n: int = -1) -> bytes:
        if self.closed:
            raise ValueError("I/O operation on closed file.")

        if n == -1:
            return b"".join(stream.read() for stream in self._streams)

        while self._index < len(self._streams):
            data = self._streams[self._index].read(n)
            if data:
                return data
            self._index += 1

        # All streams are exhausted.
        return b""

    def readinto(self, b: bytearray | memoryview) -> int:  # type: ignore[override]
        data = self.read(len(b))
        n = len(data)
        b[:n] = data
        return n

    # Properties -------------------------------------------------------
    def readable(self) -> bool:  # pragma: no cover - trivial
        return True

    def writable(self) -> bool:  # pragma: no cover - trivial
        return False

    def seekable(self) -> bool:  # pragma: no cover - trivial
        return False

    def fileno(self) -> int:  # pragma: no cover - simple
        raise OSError("fileno")

    # Control methods --------------------------------------------------
    def close(self) -> None:  # pragma: no cover - simple delegation
        super().close()


class RecordableStream(io.RawIOBase, BinaryIO):
    """Wrap a stream, caching all data read from it."""

    def __init__(self, inner: ReadableStreamLikeOrSimilar):
        super().__init__()
        self._inner = inner
        self._buffer = bytearray()
        self._pos = 0
        self._inner_eof = False

    def get_all_data(self) -> bytes:
        """Return all data read so far."""
        return bytes(self._buffer)

    def get_complete_stream(self) -> ConcatenationStream:
        """Return a stream that will provide all the data in the original stream,
        including any data read so far.

        Calling this method closes this stream, to prevent messing up the contents of
        the concatenated stream.
        """
        concatenation = ConcatenationStream([io.BytesIO(self._buffer), self._inner])
        self.close()
        return concatenation

    # Basic IO methods -------------------------------------------------
    def read(self, n: int = -1) -> bytes:
        if self.closed:
            raise ValueError("I/O operation on closed file.")

        if n == -1:
            data = self._buffer[self._pos :]
            self._pos = len(self._buffer)
            chunk = self._inner.read()
            self._buffer.extend(chunk)
            self._pos = len(self._buffer)
            self._inner_eof = True
            return bytes(data) + chunk

        remaining = n
        data = bytearray()

        available = len(self._buffer) - self._pos
        if available > 0:
            take = min(available, remaining)
            data.extend(self._buffer[self._pos : self._pos + take])
            self._pos += take
            remaining -= take

        if remaining > 0 and not self._inner_eof:
            chunk = self._inner.read(remaining)
            if not chunk:
                self._inner_eof = True
            self._buffer.extend(chunk)
            self._pos += len(chunk)
            data.extend(chunk)

        return bytes(data)

    def readinto(self, b: bytearray | memoryview) -> int:  # type: ignore[override]
        data = self.read(len(b))
        n = len(data)
        b[:n] = data
        return n

    # Seek/Tell --------------------------------------------------------
    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_CUR:
            offset = self._pos + offset
        elif whence == io.SEEK_END:
            raise io.UnsupportedOperation("seek to end")
        elif whence != io.SEEK_SET:
            raise ValueError(f"Invalid whence: {whence}")

        if offset < 0:
            raise io.UnsupportedOperation("seek outside recorded region")

        while offset > len(self._buffer):
            chunk = self._inner.read(offset - len(self._buffer))
            if not chunk:
                self._inner_eof = True
                break
            self._buffer.extend(chunk)

        self._pos = offset
        return self._pos

    def tell(self) -> int:
        return self._pos

    # Properties -------------------------------------------------------
    def readable(self) -> bool:  # pragma: no cover - trivial
        return True

    def writable(self) -> bool:  # pragma: no cover - trivial
        return False

    def seekable(self) -> bool:  # pragma: no cover - trivial
        return True

    # Control methods --------------------------------------------------
    def close(self) -> None:  # pragma: no cover - simple delegation
        # Do not close the underlying stream, as it may be used by other code.
        super().close()


class RewindableStreamWrapper:
    def __init__(self, stream: ReadableStreamLikeOrSimilar):
        self._stream = stream
        self._start_pos: int | None = None
        self._recordable_stream: RecordableStream | None = None

        if is_seekable(stream):
            self._start_pos = stream.tell()  # type: ignore[union-attr]
        else:
            self._recordable_stream = RecordableStream(stream)

    def get_stream(self) -> BinaryIO:
        if self._recordable_stream is not None:
            return self._recordable_stream
        return ensure_binaryio(self._stream)

    def get_rewinded_stream(self) -> BinaryIO:
        if self._start_pos is not None:
            self._stream.seek(self._start_pos)  # type: ignore[union-attr]
            return ensure_binaryio(self._stream)

        assert self._recordable_stream is not None
        return self._recordable_stream.get_complete_stream()


class SlicingStream(io.RawIOBase, BinaryIO):
    def __init__(
        self, stream: BinaryIO, start: int | None = None, length: int | None = None
    ):
        """
        Wraps a binary stream to provide a view (slice) of a portion of it.

        If the underlying stream `stream` is seekable:
        - If `start` is provided, it defines the absolute offset in the underlying
          stream where the slice begins. If `start` is None, the slice begins
          at the underlying stream's current position.
        - If `length` is provided, it defines the maximum number of bytes in the
          slice. If `length` is None, the slice extends to the end of the
          underlying stream.
        - Seeking within this stream will be relative to the start of the slice.

        If the underlying stream `stream` is not seekable:
        - `start` must be None (or not provided), as seeking to an absolute
          position is not possible. The slice implicitly starts from the current
          position of the non-seekable stream.
        - If `length` is provided, it defines the maximum number of bytes that
          can be read from the slice. If `length` is None, the slice will
          read until the underlying non-seekable stream is exhausted.
        - Seeking is not supported.

        Args:
            stream: The underlying binary IO stream.
            start: The absolute starting position of the slice in the underlying
                   stream. If None and stream is seekable, uses current position.
                   Must be None if stream is not seekable.
            length: The maximum length of the slice. If None, reads until the end
                    of the underlying stream (or until the non-seekable stream ends).
        """
        super().__init__()
        self._stream = stream
        self._seekable = is_seekable(stream)
        self._initial_stream_pos: int | None = None

        if self._seekable:
            self._initial_stream_pos = stream.tell()
            if start is None:
                start = self._initial_stream_pos
            # Position the underlying stream at the start of the slice
            if self._initial_stream_pos != start:
                stream.seek(start)
        else:
            if start is not None:
                raise ValueError(
                    "Cannot slice a non-seekable stream with a start position"
                )
            # For non-seekable streams, start is implicitly the current position.
            # We don't store it as it's not an absolute position we can return to.

        self._start = start  # Absolute start in the underlying stream if seekable
        self._length = length
        self._pos = 0  # Current position relative to the start of the slice

    def _compute_bytes_to_read(self, n: int) -> int:
        if self._length is not None:
            remaining = self._length - self._pos
            if n == -1:
                return remaining
            return min(n, remaining)
        return n

    def read(self, n: int = -1) -> bytes:
        n = self._compute_bytes_to_read(n)
        if n == 0:
            return b""

        data = self._stream.read(n)
        self._pos += len(data)
        return data

    def readinto(self, b: bytearray | memoryview) -> int:  # type: ignore[override]
        buf = self.read(len(b))
        b[: len(buf)] = buf
        return len(buf)

    def tell(self) -> int:
        """Return the current position within the slice."""
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        """
        Change the stream position within the current slice.

        Args:
            offset: The offset in bytes.
            whence: The reference point for the offset.
                io.SEEK_SET (0): Start of the slice.
                io.SEEK_CUR (1): Current position within the slice.
                io.SEEK_END (2): End of the slice (if length is defined).

        Returns:
            The new absolute position within the slice.

        Raises:
            ValueError: If whence is invalid.
            io.UnsupportedOperation: If the stream is not seekable, or if trying
                                     to seek outside slice boundaries in some cases.
        """
        if not self._seekable:
            raise io.UnsupportedOperation("seek on non-seekable stream")

        start_abs = ensure_not_none(self._start)
        current_abs_pos_in_stream = start_abs + self._pos
        new_relative_pos: int

        if whence == io.SEEK_SET:
            new_relative_pos = offset
        elif whence == io.SEEK_CUR:
            new_relative_pos = self._pos + offset
        elif whence == io.SEEK_END:
            if self._length is None:
                # Seeking from SEEK_END is problematic if length is not defined.
                # We could try to seek to the end of the underlying stream,
                # but that might be very far.
                # For now, let's disallow SEEK_END if length is not set.
                # Alternatively, one could argue it should behave like underlying stream's SEEK_END.
                # However, the slice abstraction implies boundaries.
                # Let underlying stream handle if offset is 0, effectively asking for its size.
                if offset == 0:
                    # This will effectively give the size of the underlying stream
                    # relative to our start, which can act as an unbounded length.
                    # We don't set self._length here, but it informs the possible _pos.
                    underlying_end = self._stream.seek(0, io.SEEK_END)
                    self._stream.seek(current_abs_pos_in_stream)  # restore position
                    new_relative_pos = underlying_end - start_abs + offset

                else:
                    raise io.UnsupportedOperation(
                        "SEEK_END is not supported when slice length is not defined "
                        "and offset is non-zero"
                    )

            else:
                new_relative_pos = self._length + offset
        else:
            raise ValueError(f"Invalid whence: {whence}")

        if new_relative_pos < 0:
            raise ValueError("Negative seek position")

        if self._length is not None and new_relative_pos > self._length:
            # Allow seeking past the defined end, but reads will be clamped.
            # This matches behavior of io.BytesIO.
            pass

        # Calculate the new absolute position in the underlying stream
        new_abs_pos_in_stream = start_abs + new_relative_pos

        # Perform the actual seek on the underlying stream
        self._stream.seek(new_abs_pos_in_stream)
        self._pos = new_relative_pos
        return self._pos

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    def seekable(self) -> bool:
        return self._seekable


def fix_stream_start_position(stream: BinaryIO) -> BinaryIO:
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
