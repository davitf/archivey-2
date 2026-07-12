"""Lock-wrapping stream for library-owned seek-before-read shared handles."""

from __future__ import annotations

import io
import threading
from typing import TYPE_CHECKING, BinaryIO

from archivey.internal.streams.streamtools.base import (
    DelegatingStream,
    ReadOnlyIOStream,
)
from archivey.internal.streams.streamtools.binaryio import is_seekable

if TYPE_CHECKING:
    from _typeshed import WriteableBuffer


class LockedStream(DelegatingStream):
    """Hold ``lock`` across each shared-handle operation on ``inner``.

    Used by TAR/ISO under ``MemberStreams.CONCURRENT`` so library seek-then-read
    sequences on a shared fileobj cannot interleave. Archivey buffering/error wrappers
    sit *outside* this layer.
    """

    def __init__(self, inner: BinaryIO, lock: threading.Lock | threading.RLock) -> None:
        super().__init__(inner)
        self._lock = lock
        self._inner_seekable = is_seekable(inner)

    def read(self, n: int = -1, /) -> bytes:
        with self._lock:
            return self._inner.read(n)

    def readinto(self, b: WriteableBuffer, /) -> int:
        with self._lock:
            readinto = getattr(self._inner, "readinto", None)
            if readinto is None:
                mv = memoryview(b).cast("B")
                data = self._inner.read(len(mv))
                mv[: len(data)] = data
                return len(data)
            return readinto(b)

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        if not self._inner_seekable:
            raise io.UnsupportedOperation("seek")
        with self._lock:
            return self._inner.seek(offset, whence)

    def tell(self, /) -> int:
        with self._lock:
            return self._inner.tell()

    def seekable(self) -> bool:
        return self._inner_seekable

    def close(self) -> None:
        if self.closed:
            return
        try:
            with self._lock:
                self._inner.close()
        finally:
            # Mark the wrapper closed without re-entering the lock via super().close()
            # if DelegatingStream.close also closes inner — close inner once under lock.
            ReadOnlyIOStream.close(self)


class CloseLockedStream(DelegatingStream):
    """Hold ``lock`` only across ``close``; leave read/seek to the inner stream.

    Used by ZIP under ``MemberStreams.CONCURRENT``: stdlib ``zipfile`` serializes
    shared-fp seek/read via ``_SharedFile``, but ``_fileRefCnt`` on open/close is
    unlocked and races under free-threaded CPython. Serializing open + close is enough;
    holding the lock across reads would needlessly serialize independent decompressors.
    """

    def __init__(self, inner: BinaryIO, lock: threading.Lock | threading.RLock) -> None:
        super().__init__(inner)
        self._lock = lock

    def close(self) -> None:
        if self.closed:
            return
        try:
            with self._lock:
                self._inner.close()
        finally:
            ReadOnlyIOStream.close(self)
