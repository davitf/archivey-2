"""LockedStream unit tests for the TAR/ISO shared-handle wrapper."""

from __future__ import annotations

import io
import threading

import pytest

from archivey.internal.streams.streamtools import LockedStream

pytestmark = pytest.mark.concurrent_reader


class _SeekBeforeRead:
    """Fake library stream: each read seeks then reads from a shared cursor."""

    def __init__(self, shared: io.BytesIO, start: int, length: int) -> None:
        self._shared = shared
        self._start = start
        self._length = length
        self._pos = 0
        self.closed = False

    def read(self, n: int = -1) -> bytes:
        self._shared.seek(self._start + self._pos)
        if n < 0:
            n = self._length - self._pos
        n = min(n, self._length - self._pos)
        data = self._shared.read(n)
        self._pos += len(data)
        return data

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            self._pos = offset
        elif whence == io.SEEK_CUR:
            self._pos += offset
        else:
            self._pos = self._length + offset
        return self._pos

    def tell(self) -> int:
        return self._pos

    def seekable(self) -> bool:
        return True

    def close(self) -> None:
        self.closed = True


def test_locked_stream_interleaved_reads() -> None:
    shared = io.BytesIO(b"AAAABBBBCCCC")
    lock = threading.Lock()
    a = LockedStream(_SeekBeforeRead(shared, 0, 4), lock)
    b = LockedStream(_SeekBeforeRead(shared, 4, 4), lock)
    assert a.read(2) == b"AA"
    assert b.read(2) == b"BB"
    assert a.read() == b"AA"
    assert b.read() == b"BB"
    a.close()
    b.close()


def test_tar_iso_concurrent_open_uses_lock(tmp_path) -> None:
    import tarfile

    from archivey import MemberStreams, open_archive

    path = tmp_path / "a.tar"
    with tarfile.open(path, "w") as t:
        for name, data in (("a.txt", b"aaaa"), ("b.txt", b"bbbb")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))

    with open_archive(
        path, member_streams=MemberStreams.CONCURRENT | MemberStreams.SEEKABLE
    ) as ar:
        s1 = ar.open("a.txt")
        s2 = ar.open("b.txt")
        assert s1.read(2) == b"aa"
        assert s2.read(2) == b"bb"
        assert s1.read() == b"aa"
        assert s2.read() == b"bb"
        s1.close()
        s2.close()
