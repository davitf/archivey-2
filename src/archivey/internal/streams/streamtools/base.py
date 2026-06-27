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

This module is part of the codec-/format-agnostic ``streamtools`` core: it imports nothing from
the rest of ``archivey``.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING, Any, BinaryIO

if TYPE_CHECKING:
    from _typeshed import WriteableBuffer


class ReadOnlyIOStream(io.RawIOBase, BinaryIO):
    """Base for a read-only ``BinaryIO``: subclasses implement ``read`` (and what they change)."""

    def read(self, n: int = -1, /) -> bytes:
        raise NotImplementedError  # a subclass must provide read()

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


class DelegatingStream(ReadOnlyIOStream):
    """A read-only wrapper around one inner ``BinaryIO``, forwarding to it by default.

    Subclasses override only the method whose behavior they change (e.g. just ``seek`` to add a
    warning, or just ``close`` to add a cleanup guard).
    """

    def __init__(self, inner: BinaryIO) -> None:
        super().__init__()
        self._inner = inner

    def read(self, n: int = -1, /) -> bytes:
        return self._inner.read(n)

    def readinto(self, b: "WriteableBuffer", /) -> int:
        # Zero-copy passthrough when the inner exposes readinto; else the read()-based base.
        inner_readinto = getattr(self._inner, "readinto", None)
        if inner_readinto is not None:
            return inner_readinto(b)
        return super().readinto(b)

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        return self._inner.seek(offset, whence)

    def tell(self, /) -> int:
        return self._inner.tell()

    def seekable(self) -> bool:
        return self._inner.seekable()

    def close(self) -> None:
        if self.closed:
            return
        self._inner.close()
        super().close()
