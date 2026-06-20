"""Adapt arbitrary caller-provided objects to the uniform ``BinaryIO`` the library relies on.

Sources reach the library in many shapes — a path, a real file object, a ``BytesIO``, or a
codec library's file-like that is *almost* a ``BinaryIO`` (e.g. missing ``readinto``). This
module is the single place that classifies those objects (``is_filename`` / ``is_stream`` /
``is_seekable``) and coerces them to a consistent ``BinaryIO`` (``ensure_binaryio`` /
``ensure_bufferedio`` / ``BinaryIOWrapper``), so the rest of the stream layer can assume one
interface.
"""

from __future__ import annotations

import io
import mmap
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
    lack a ``seekable()`` method are conservatively treated as non-seekable — except known
    types we can assert statically (``mmap``). We deliberately do *not* probe by calling
    ``seek()``: that would make this predicate side-effecting (it's called on hot paths and
    on streams someone is mid-read on), and a no-op probe isn't even conclusive (a stream
    can accept ``seek(0, SEEK_CUR)`` yet not reposition).
    """
    if isinstance(stream, io.BufferedReader):
        return is_seekable(stream.raw)
    # mmap is always seekable but (before Python 3.13) exposes no seekable() method and is
    # not an io.IOBase, so the generic check below would miss it.
    if isinstance(stream, mmap.mmap):
        return True
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
    """Adapt an object that exposes a *partial* file API to the full ``BinaryIO`` interface.

    Most streams never need this. Every stdlib stream — ``open()`` (``BufferedReader`` /
    ``FileIO``), ``BytesIO``, ``GzipFile`` / ``BZ2File`` / ``LZMAFile``, zipfile's
    ``ZipExtFile``, tarfile's ``ExFileObject`` — and modern network responses
    (``http.client.HTTPResponse``, ``urllib3>=2`` ``HTTPResponse``) subclass ``io.IOBase``,
    so :func:`is_stream` passes them through unwrapped.

    Wrapping is for objects that implement a few file methods *without* subclassing
    ``io.IOBase``, so the type checker won't accept them as ``BinaryIO`` and they may be
    missing methods we rely on. Concretely:

    - ``urllib3<2`` ``HTTPResponse`` (``requests``' ``response.raw`` on older installs):
      has ``read()`` but is not an ``io.IOBase`` and historically lacked ``readinto`` /
      ``seekable``.
    - ``py7zr`` / ``rarfile`` member handles (Phase 7) and arbitrary user objects exposing
      just ``read()`` (and maybe ``seek()``).

    Delegation is plain (each method forwards to ``self._raw``) rather than rebinding
    methods onto the instance: rebinding saves one attribute lookup per call but mutates the
    instance and defeats type checking, for no measurable gain on the large reads that
    matter. The wrapper deliberately does **not** close the wrapped object (it is often a
    temporary view onto a stream someone else owns).
    """

    def __init__(self, raw: Any) -> None:
        super().__init__()
        self._raw = raw

    def read(self, size: int = -1, /) -> bytes:
        data = self._raw.read(size)
        if data is None:
            # A read() returns None only for a *non-blocking* stream that has no data
            # available right now — never at EOF, where blocking and non-blocking streams
            # alike return b"". archivey's readers pull synchronously and cannot make
            # progress on a non-blocking source, so surface that explicitly instead of
            # fabricating b"" (which would look like EOF and silently truncate the data).
            raise BlockingIOError(
                "underlying stream returned no data without reaching EOF (non-blocking "
                "stream?); archivey requires a blocking stream"
            )
        return data

    def readinto(self, b: "WriteableBuffer", /) -> int:
        raw_readinto = getattr(self._raw, "readinto", None)
        if raw_readinto is not None:
            # Some partial file-likes advertise readinto but raise when actually called
            # (e.g. NotImplementedError); fall back to read() in that case.
            try:
                n = raw_readinto(b)
            except (NotImplementedError, io.UnsupportedOperation):
                pass
            else:
                if n is None:
                    # Same non-blocking case as read() above; don't report a 0-byte read.
                    raise BlockingIOError(
                        "underlying stream returned no data without reaching EOF "
                        "(non-blocking stream?); archivey requires a blocking stream"
                    )
                return n
        mv = memoryview(b).cast("B")
        data = self.read(len(mv))
        mv[: len(data)] = data
        return len(data)

    def write(self, data: Any, /) -> int:
        write = getattr(self._raw, "write", None)
        if write is None:
            raise io.UnsupportedOperation("write")
        return write(data)

    def readable(self) -> bool:
        # Prefer the stream's own answer; fall back to "does it expose a reader?" for
        # partial file-likes that don't implement readable().
        raw_readable = getattr(self._raw, "readable", None)
        if raw_readable is not None:
            return bool(raw_readable())
        return hasattr(self._raw, "read") or hasattr(self._raw, "readinto")

    def writable(self) -> bool:
        # hasattr(raw, "write") is NOT a reliable signal: every io.IOBase defines write()
        # even when opened read-only (it raises io.UnsupportedOperation — GzipFile raises
        # OSError). The honest answer is the stream's own writable(); only fall back to
        # hasattr() for partial file-likes that don't implement writable() (e.g. urllib3's
        # HTTPResponse, which omits write() entirely).
        raw_writable = getattr(self._raw, "writable", None)
        if raw_writable is not None:
            return bool(raw_writable())
        return hasattr(self._raw, "write")

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        seek = getattr(self._raw, "seek", None)
        if seek is None:
            raise io.UnsupportedOperation("seek")
        pos = seek(offset, whence)
        if pos is None:
            # Some objects' seek() returns None instead of the new position (mmap before
            # Python 3.13); recover it via tell() so we honour the BinaryIO -> int contract.
            tell = getattr(self._raw, "tell", None)
            return tell() if tell is not None else offset
        return pos

    def tell(self, /) -> int:
        tell = getattr(self._raw, "tell", None)
        if tell is None:
            raise io.UnsupportedOperation("tell")
        return tell()

    def seekable(self) -> bool:
        return is_seekable(self._raw)

    def close(self) -> None:
        # Intentionally does not close the wrapped stream (often a temporary view).
        super().close()

    def __repr__(self) -> str:
        return f"BinaryIOWrapper({self._raw!r})"


def ensure_binaryio(obj: Any) -> BinaryIO:
    """Return ``obj`` as a ``BinaryIO``, wrapping it only if it doesn't already qualify.

    The result is a valid ``BinaryIO`` but not necessarily an ``io.RawIOBase`` (an
    already-qualifying ``BytesIO`` is a ``BufferedIOBase``, returned unchanged). Callers
    that specifically need a ``RawIOBase`` — e.g. to feed ``io.BufferedReader`` — should use
    :func:`ensure_bufferedio`, which handles that requirement internally.
    """
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

    An already-buffered stream is returned unchanged; otherwise it is wrapped in a
    non-closing ``BufferedReader``. ``io.BufferedReader`` requires its underlying object to
    be an ``io.RawIOBase`` (it rejects a merely stream-like object), so a non-``RawIOBase``
    source is first adapted via :class:`BinaryIOWrapper` (which *is* a ``RawIOBase``) — this
    is why we branch on ``RawIOBase`` here rather than calling :func:`ensure_binaryio`,
    whose result may be a ``BufferedIOBase`` that ``BufferedReader`` would reject.
    """
    if isinstance(obj, io.BufferedIOBase):
        return obj
    raw: io.RawIOBase
    if isinstance(obj, io.RawIOBase):
        raw = obj
    else:
        raw = BinaryIOWrapper(obj)
    return _NonClosingBufferedReader(raw)
