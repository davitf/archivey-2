"""Shared bases for archivey's read-only stream wrappers.

``io.RawIOBase``'s defaults are the wrong way round for a *wrapper*: ``readable``/``writable``/
``seekable`` default to ``False``, and ``read``/``readall`` are implemented in terms of
``readinto``. Every read-only wrapper would otherwise re-declare ``readable()->True`` /
``writable()->False`` and supply a ``readinto``. These bases invert that once:

- :class:`ReadOnlyIOStream` provides the read-only surface (``readable``/``writable``/``write``)
  and a single canonical ``readinto``/``readall`` built on the subclass's ``read``. A subclass
  implements ``read`` (+ whatever it actually changes); ``read`` is left abstract so a subclass
  that forgets it fails loudly instead of looping through ``RawIOBase``.
- :class:`DelegatingStream` additionally holds one inner ``BinaryIO`` and forwards
  ``read``/``readinto``/``seek``/``tell``/``seekable``/``close`` to it, so a wrapper that only
  changes one operation overrides just that method.

This module is part of the codec-/format-agnostic ``streamtools`` core: it imports only from
``streamtools`` itself (``is_seekable``), nothing from the rest of ``archivey``.
"""

from __future__ import annotations

import abc
import io
from typing import TYPE_CHECKING, Any, BinaryIO

from archivey.internal.streams.streamtools.binaryio import is_seekable

if TYPE_CHECKING:
    from _typeshed import WriteableBuffer


class ReadOnlyIOStream(io.RawIOBase, BinaryIO):
    """Base for a read-only ``BinaryIO``: subclasses implement ``read`` (and what they change).

    Deliberately defines only the *read-only surface* (``read``/``readinto``/``readall`` +
    ``readable``/``writable``/``write``). It does **not** define ``seek``/``tell``/``seekable``/
    ``close``: those genuinely vary per wrapper — sequential vs. seekable, owns-its-inner vs. a
    non-owning view — so subclasses declare them, or inherit ``RawIOBase``'s non-seekable
    defaults (``seekable()->False``, ``seek`` raising). :class:`DelegatingStream` supplies the
    forwarding versions for wrappers that do own one inner stream.
    """

    @abc.abstractmethod
    def read(self, n: int = -1, /) -> bytes:
        # @abstractmethod is an intent/static-check signal that subclasses must provide read().
        # It does NOT prevent instantiation here: io.RawIOBase's C-level __new__ ignores
        # __abstractmethods__ (a subclass without read() still constructs), so this body is the
        # actual runtime guard — a forgotten read() fails loudly instead of looping via RawIOBase.
        raise NotImplementedError

    def readinto(self, b: "WriteableBuffer", /) -> int:
        """Canonical ``readinto``: read into ``b`` via the subclass's ``read``."""
        mv = memoryview(b).cast("B")
        data = self.read(len(mv))
        mv[: len(data)] = data
        return len(data)

    def readall(self) -> bytes:
        chunks = bytearray()
        while True:
            chunk = self.read(io.DEFAULT_BUFFER_SIZE)
            if not chunk:
                break
            chunks.extend(chunk)
        return bytes(chunks)

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    def write(self, b: Any, /) -> int:
        raise io.UnsupportedOperation("write")

    @property
    def mode(self) -> str:
        # typing.IO declares an abstract `mode` property whose stub body returns None at
        # runtime; libraries that duck-type it (pycdlib does `'b' not in fp.mode`) then
        # crash on our wrappers. Every stream here is read-only binary by construction.
        return "rb"


class DelegatingStream(ReadOnlyIOStream):
    """A read-only wrapper around one inner ``BinaryIO``, forwarding to it by default.

    Subclasses override only the method whose behavior they change (e.g. just ``seek`` to add a
    warning, or just ``close`` to add a cleanup guard).

    **Consistency caveat (``readinto_passthrough``).** By default ``readinto`` forwards straight
    to ``inner.readinto`` (zero-copy), which *bypasses this class's ``read``*. That is correct
    for a plain delegator, but a subclass that overrides ``read`` with a side effect (tracking
    bytes, hashing, a check at EOF) would have that side effect skipped on ``readinto``-driven
    reads. Such a subclass MUST pass ``readinto_passthrough=False``, which routes ``readinto``
    through ``read`` (the :class:`ReadOnlyIOStream` implementation) so the override always runs.
    (We use an explicit flag rather than auto-detecting an overridden ``read``: a plain
    pass-through override of ``read`` should keep the zero-copy path, and silent auto-detection
    would make that choice invisible and bug-prone.)
    """

    def __init__(self, inner: BinaryIO, *, readinto_passthrough: bool = True) -> None:
        super().__init__()
        self._inner = inner
        self._readinto_passthrough = readinto_passthrough

    def read(self, n: int = -1, /) -> bytes:
        return self._inner.read(n)

    def readinto(self, b: "WriteableBuffer", /) -> int:
        # Zero-copy passthrough when allowed and the inner exposes readinto; otherwise route
        # through self.read() (the read()-based base), so an overridden read() is not bypassed.
        if self._readinto_passthrough:
            inner_readinto = getattr(self._inner, "readinto", None)
            if inner_readinto is not None:
                return inner_readinto(b)
        return super().readinto(b)

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        return self._inner.seek(offset, whence)

    def tell(self, /) -> int:
        return self._inner.tell()

    def seekable(self) -> bool:
        # is_seekable() handles the edge cases a bare inner.seekable() misses (a BufferedReader
        # over a non-seekable raw; a pipe that reports seekable()=True but cannot reposition).
        return is_seekable(self._inner)

    def close(self) -> None:
        if self.closed:
            return
        self._inner.close()
        super().close()
