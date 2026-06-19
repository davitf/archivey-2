"""Stream compatibility helpers.

Ported as a unit from DEV's scattered helpers (``core.py`` / ``io_helpers.py`` /
``single_file_reader.py``), with one deliberate change: ``BinaryIOWrapper`` is rewritten
with **plain delegation** rather than DEV's ``self.read = self._raw.read`` method-swap
trick. The swap optimised away one attribute lookup per call at the cost of mutating the
instance and confusing type checkers; plain delegation is simpler and benchmarks
equivalently on large reads (see PLAN's `BinaryIOWrapper` risk note).
"""

from __future__ import annotations

import io
import os
from typing import TYPE_CHECKING, Any, BinaryIO, Protocol, TypeGuard, runtime_checkable

from archivey.internal.logs import streams as logger

if TYPE_CHECKING:
    from _typeshed import WriteableBuffer


@runtime_checkable
class ReadableStream(Protocol):
    """Minimal readable-binary protocol: just ``read``."""

    def read(self, n: int = ..., /) -> bytes: ...


def read_exact(stream: ReadableStream, n: int) -> bytes:
    """Read exactly ``n`` bytes, or fewer only if the stream ends first.

    Unlike a single ``read(n)`` (which may legally return a short chunk), this loops
    until ``n`` bytes are gathered or EOF is hit — the behaviour parsers want when
    pulling fixed-size headers.
    """
    if n < 0:
        raise ValueError("n must be non-negative")

    data = bytearray()
    while len(data) < n:
        chunk = stream.read(n - len(data))
        if not chunk:
            break
        data.extend(chunk)
    return bytes(data)


def is_seekable(stream: Any) -> bool:
    """Whether ``stream`` can actually seek.

    A ``BufferedReader`` reports its own ``seekable()`` as ``True`` even when wrapping a
    non-seekable raw stream, so unwrap to the underlying raw object first. Streams that
    lack ``seekable()`` entirely are treated as non-seekable.
    """
    if isinstance(stream, io.BufferedReader):
        return is_seekable(stream.raw)
    seekable = getattr(stream, "seekable", None)
    if seekable is None:
        logger.debug("Stream %r has no seekable() method; treating as non-seekable", stream)
        return False
    return bool(seekable())


# Methods/properties a real BinaryIO exposes; used by is_stream() to decide whether an
# object can be passed through unwrapped.
_IO_METHODS = (
    "read",
    "seek",
    "tell",
    "close",
    "readable",
    "writable",
    "seekable",
    "readinto",
)


def is_filename(obj: Any) -> TypeGuard[str | bytes | os.PathLike]:
    """Whether ``obj`` is a path-like (str / bytes / ``os.PathLike``)."""
    return isinstance(obj, (str, bytes, os.PathLike))


def is_stream(obj: Any) -> TypeGuard[BinaryIO]:
    """Whether ``obj`` already satisfies the ``BinaryIO`` interface we rely on.

    ``io.IOBase`` instances qualify directly; anything else must expose the full method
    set in :data:`_IO_METHODS` and a ``closed`` attribute.
    """
    if isinstance(obj, io.IOBase):
        return True
    if is_filename(obj):
        return False
    if not all(callable(getattr(obj, m, None)) for m in _IO_METHODS):
        return False
    return hasattr(obj, "closed")


class BinaryIOWrapper(io.RawIOBase, BinaryIO):
    """Adapt an object that is *almost* a ``BinaryIO`` to the real interface.

    Some codec libraries return file-likes that miss ``readinto`` or otherwise don't
    satisfy the type checker. This wraps them with straightforward delegation; missing
    ``readinto`` falls back to ``read``. It deliberately does **not** close the wrapped
    object (it is often a temporary view).
    """

    def __init__(self, raw: Any) -> None:
        super().__init__()
        self._raw = raw

    def read(self, size: int = -1, /) -> bytes:
        data = self._raw.read(size)
        # A blocking stream returns b"" at EOF; normalise a None (non-blocking "no data
        # yet") to b"" so the wrapper presents a plain blocking BinaryIO.
        return data if data is not None else b""

    def readinto(self, b: "WriteableBuffer", /) -> int:
        raw_readinto = getattr(self._raw, "readinto", None)
        if raw_readinto is not None:
            try:
                result = raw_readinto(b)
                if result is not None:
                    return result
            except (NotImplementedError, io.UnsupportedOperation):
                pass
        mv = memoryview(b).cast("B")
        data = self.read(len(mv))
        mv[: len(data)] = data
        return len(data)

    def write(self, data: Any, /) -> int:
        write = getattr(self._raw, "write", None)
        if write is None:
            raise io.UnsupportedOperation("write")
        return write(data)

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        seek = getattr(self._raw, "seek", None)
        if seek is None:
            raise io.UnsupportedOperation("seek")
        return seek(offset, whence)

    def tell(self, /) -> int:
        tell = getattr(self._raw, "tell", None)
        if tell is None:
            raise io.UnsupportedOperation("tell")
        return tell()

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return hasattr(self._raw, "write")

    def seekable(self) -> bool:
        return is_seekable(self._raw)

    def close(self) -> None:
        # Intentionally does not close the wrapped stream (often a temporary view).
        super().close()

    def __repr__(self) -> str:
        return f"BinaryIOWrapper({self._raw!r})"


def ensure_binaryio(obj: Any) -> BinaryIO:
    """Return ``obj`` as a ``BinaryIO``, wrapping it only if it doesn't already qualify."""
    if is_stream(obj):
        return obj
    logger.debug("Wrapping %r in BinaryIOWrapper to satisfy the BinaryIO interface", obj)
    return BinaryIOWrapper(obj)


class _NonClosingBufferedReader(io.BufferedReader):
    """A ``BufferedReader`` that detaches instead of closing its raw stream.

    Used when we temporarily buffer a caller-owned stream (e.g. to peek a header): the
    buffer must not close a stream we don't own.
    """

    def close(self) -> None:
        if not self.closed:
            self.detach()


def ensure_bufferedio(obj: Any) -> io.BufferedIOBase:
    """Return ``obj`` as a buffered reader, without taking ownership of it.

    A raw stream is wrapped in a non-closing ``BufferedReader``; an already-buffered
    stream is returned unchanged.
    """
    if isinstance(obj, io.BufferedIOBase):
        return obj
    raw: io.RawIOBase
    if isinstance(obj, io.RawIOBase):
        raw = obj
    else:
        raw = BinaryIOWrapper(obj)
    return _NonClosingBufferedReader(raw)


def fix_stream_start_position(stream: BinaryIO) -> BinaryIO:
    """Make a stream behave as if its current position were 0.

    If ``stream`` is seekable and already at offset 0, it is returned unchanged. If it is
    seekable but positioned mid-stream, it is wrapped in a slice so the consumer (a codec
    library that assumes ``tell() == 0``) sees a clean origin. Non-seekable streams are
    returned as-is (the caller can only read forward anyway).
    """
    if not is_seekable(stream):
        return stream
    start_pos = stream.tell()
    if start_pos == 0:
        return stream
    # Imported lazily to avoid a module import cycle (slice imports compat).
    from archivey.internal.streams.slice import SlicingStream

    return SlicingStream(stream, start=start_pos)
