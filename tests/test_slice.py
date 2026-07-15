"""Unit tests for ``SlicingStream`` and ``fix_stream_start_position`` (``streams/streamtools/slice.py``).

These are low-level building blocks every container backend relies on, so they get focused
corner-case coverage (per CONTRIBUTING's narrow exception for stream primitives).
"""

from __future__ import annotations

import io
import threading
import zipfile
import zlib
from pathlib import Path

import pytest

from archivey.internal.streams.streamtools import (
    SlicingStream,
    fix_stream_start_position,
)
from tests.streams_util import NonSeekableBytesIO

DATA = b"0123456789abcdefghijklmnopqrstuvwxyz"


class TestSlicingStream:
    def test_read_with_start_and_length(self) -> None:
        sliced = SlicingStream(io.BytesIO(DATA), start=5, length=10)
        assert sliced.read(3) == b"567"
        assert sliced.tell() == 3
        assert sliced.read() == b"89abcde"
        assert sliced.tell() == 10
        assert sliced.read(5) == b""

    def test_read_start_only_reads_to_end(self) -> None:
        sliced = SlicingStream(io.BytesIO(DATA), start=10)
        assert sliced.read() == DATA[10:]

    def test_read_length_only_from_current_position(self) -> None:
        underlying = io.BytesIO(DATA)
        underlying.seek(7)
        sliced = SlicingStream(underlying, length=10)
        assert sliced.read() == DATA[7:17]
        assert sliced.tell() == 10

    def test_read_spanning_then_clamped_at_length(self) -> None:
        sliced = SlicingStream(io.BytesIO(DATA), start=0, length=20)
        assert sliced.read(8) == DATA[:8]
        assert sliced.read(100) == DATA[8:20]  # clamped to the slice end
        assert sliced.read(1) == b""

    def test_read_zero_returns_empty(self) -> None:
        sliced = SlicingStream(io.BytesIO(DATA), start=0, length=10)
        assert sliced.read(0) == b""
        assert sliced.tell() == 0

    def test_empty_slice(self) -> None:
        sliced = SlicingStream(io.BytesIO(DATA), start=5, length=0)
        assert sliced.read() == b""
        assert sliced.read(10) == b""

    def test_slice_larger_than_underlying(self) -> None:
        sliced = SlicingStream(io.BytesIO(DATA[:10]), start=0, length=20)
        assert sliced.read() == DATA[:10]
        assert sliced.read(5) == b""

    def test_readinto(self) -> None:
        sliced = SlicingStream(io.BytesIO(DATA), start=5, length=10)
        buf = bytearray(4)
        n = sliced.readinto(buf)
        assert n == 4
        assert bytes(buf) == DATA[5:9]

    def test_seek_set_cur_end(self) -> None:
        sliced = SlicingStream(io.BytesIO(DATA), start=10, length=10)
        assert sliced.seek(3) == 3
        assert sliced.read(2) == DATA[13:15]
        assert sliced.seek(-2, io.SEEK_CUR) == 3
        assert sliced.read(4) == DATA[13:17]
        assert sliced.seek(-1, io.SEEK_END) == 9
        assert sliced.read(5) == DATA[19:20]

    def test_seek_past_end_then_empty_read(self) -> None:
        sliced = SlicingStream(io.BytesIO(DATA), start=10, length=10)
        assert sliced.seek(100) == 100
        assert sliced.read(1) == b""

    def test_seek_negative_raises(self) -> None:
        sliced = SlicingStream(io.BytesIO(DATA), start=10, length=10)
        with pytest.raises(ValueError, match="Negative seek position"):
            sliced.seek(-5)

    def test_seek_cur_underflow_clamps_like_bytesio(self) -> None:
        # BytesIO clamps SEEK_CUR/SEEK_END underflow to the origin; only SEEK_SET raises.
        sliced = SlicingStream(io.BytesIO(DATA), start=10, length=10)
        assert sliced.seek(-100, io.SEEK_CUR) == 0
        assert sliced.tell() == 0
        assert sliced.seek(-100, io.SEEK_END) == 0

    def test_seek_end_no_length_zero_offset(self) -> None:
        sliced = SlicingStream(io.BytesIO(DATA), start=10)
        assert sliced.seek(0, io.SEEK_END) == len(DATA) - 10
        assert sliced.read(1) == b""

    def test_seek_end_no_length_nonzero_offset(self) -> None:
        # With no declared length the slice ends at the underlying EOF; SEEK_END with a
        # non-zero offset probes that end on demand and positions relative to it.
        sliced = SlicingStream(io.BytesIO(DATA), start=5)
        slice_len = len(DATA) - 5
        assert sliced.seek(-3, io.SEEK_END) == slice_len - 3
        assert sliced.read() == DATA[-3:]
        assert (
            sliced.seek(2, io.SEEK_END) == slice_len + 2
        )  # past-end allowed, like BytesIO
        assert sliced.read(1) == b""

    def test_non_seekable_no_start(self) -> None:
        sliced = SlicingStream(NonSeekableBytesIO(DATA), length=15)
        assert sliced.read(5) == DATA[:5]
        assert sliced.read() == DATA[5:15]
        assert not sliced.seekable()

    def test_non_seekable_with_start_rejected(self) -> None:
        with pytest.raises(ValueError, match="Cannot slice a non-seekable stream"):
            SlicingStream(NonSeekableBytesIO(DATA), start=5, length=10)

    def test_seek_on_non_seekable_raises(self) -> None:
        sliced = SlicingStream(NonSeekableBytesIO(DATA), length=10)
        with pytest.raises(io.UnsupportedOperation, match="seek on non-seekable"):
            sliced.seek(5)

    def test_bounded_read_all_gathers_across_short_reads(self) -> None:
        """``read()`` on a bounded slice must gather across short sized reads.

        A bounded slice must not issue an unbounded ``read(-1)`` on the inner
        stream (that would overshoot into the next member). Short positive
        ``read(n)`` results are legal and must be retried until the slice is
        drained; trailing bytes past the slice must remain unread.
        """

        class _Drip(io.RawIOBase):
            def __init__(self, data: bytes) -> None:
                self._data = data
                self.pos = 0

            def readable(self) -> bool:
                return True

            def read(self, size: int = -1) -> bytes:  # type: ignore[override]
                # Bounded SlicingStream must never ask for an unbounded drain.
                if size < 0:
                    raise AssertionError(
                        "bounded SlicingStream must not issue unbounded read(-1)"
                    )
                if self.pos >= len(self._data) or size == 0:
                    return b""
                # One byte at a time so a single read(remaining) would truncate.
                chunk = self._data[self.pos : self.pos + 1]
                self.pos += len(chunk)
                return chunk

        slice_data = DATA[5:15]
        trailing = DATA[15:25]
        inner = _Drip(slice_data + trailing)
        sliced = SlicingStream(inner, length=len(slice_data))

        assert sliced.read() == slice_data
        assert sliced.tell() == len(slice_data)
        assert sliced.read(1) == b""
        assert inner.pos == len(slice_data)
        # Trailing bytes past the slice are still available on the inner stream.
        leftover = bytearray()
        while len(leftover) < len(trailing):
            chunk = inner.read(len(trailing) - len(leftover))
            assert chunk, "inner stream ended before trailing payload"
            leftover.extend(chunk)
        assert bytes(leftover) == trailing
        assert inner.pos == len(slice_data) + len(trailing)


class TestFixStreamStartPosition:
    def test_at_zero_returns_same(self) -> None:
        stream = io.BytesIO(DATA)
        assert fix_stream_start_position(stream) is stream

    def test_midstream_slices(self) -> None:
        stream = io.BytesIO(DATA)
        stream.seek(10)
        fixed = fix_stream_start_position(stream)
        assert fixed is not stream
        assert fixed.tell() == 0
        assert fixed.read(5) == DATA[10:15]

    def test_midstream_slice_has_no_name(self, tmp_path: Path) -> None:
        # fix_stream_start_position wraps mid-positioned streams; see
        # TestSlicingStreamName.test_name_not_forwarded_from_underlying for why name
        # must stay absent (pycdlib Windows + reopen-by-name footgun).
        path = tmp_path / "data.bin"
        path.write_bytes(DATA)
        with open(path, "rb") as stream:
            stream.seek(10)
            fixed = fix_stream_start_position(stream)
            assert not hasattr(fixed, "name")

    def test_non_seekable_passthrough(self) -> None:
        stream = NonSeekableBytesIO(DATA)
        assert fix_stream_start_position(stream) is stream


class TestSlicingStreamName:
    def test_name_not_forwarded_from_underlying(self, tmp_path: Path) -> None:
        """SlicingStream must not expose ``name``, even when the underlying stream has one.

        Two independent reasons — do not "fix" this by forwarding ``underlying.name``
        without considering both:

        1. **View semantics.** A slice remaps the origin (``tell()==0`` is mid-file on the
           underlying). ``stream.name`` conventionally means "reopen this path from byte 0";
           forwarding would mislead libraries that stat or ``open()`` by name into reading
           the unsliced file (embedded-archive / ``fix_stream_start_position`` cases).

        2. **Stub vs absent.** Our wrappers inherit ``typing.BinaryIO``'s stub ``name``
           (``None`` at runtime). ``hasattr(stream, 'name')`` must stay ``False`` on
           nameless views so consumers like pycdlib's Windows raw-device check
           (``fp.name.startswith(r'\\.\')``) do not crash on ``None``. Real file objects
           and ``BytesIO`` already behave this way; the slice wrapper must match.

        Callers that need a path for errors/metadata should use ``source_name()`` on the
        *original* source before wrapping (``open_archive`` captures ``archive_name`` that
        way). Logical slice length is ``SlicingStream.size``, not ``name``.
        """
        path = tmp_path / "data.bin"
        path.write_bytes(DATA)
        with open(path, "rb") as underlying:
            assert underlying.name == str(path)
            sliced = SlicingStream(underlying, start=5, length=10)
            assert not hasattr(sliced, "name")


class TestSlicingStreamSize:
    def test_size_with_declared_length(self) -> None:
        sliced = SlicingStream(io.BytesIO(DATA), start=5, length=7)
        assert sliced.size == 7

    def test_size_derived_from_cheap_underlying(self) -> None:
        sliced = SlicingStream(io.BytesIO(DATA), start=5)
        assert sliced.size == len(DATA) - 5

    def test_size_none_when_underlying_unknowable(self) -> None:
        sliced = SlicingStream(NonSeekableBytesIO(DATA), length=None)
        assert sliced.size is None


class TestSlicingStreamOwnSource:
    class _Tracked(io.BytesIO):
        closed_flag = False

        def close(self) -> None:
            self.closed_flag = True
            super().close()

    def test_default_is_non_owning(self) -> None:
        underlying = self._Tracked(DATA)
        SlicingStream(underlying, start=0, length=5).close()
        assert underlying.closed_flag is False

    def test_own_source_closes_underlying(self) -> None:
        underlying = self._Tracked(DATA)
        SlicingStream(underlying, start=0, length=5, own_source=True).close()
        assert underlying.closed_flag is True


class TestSlicingStreamLockedConstruction:
    """Locked views must not probe the shared handle unlocked at construction.

    ``io.BufferedReader.tell`` is not thread-safe. A concurrent unlocked ``tell`` while
    another thread holds the lock for seek+read corrupts the buffer — the ZIP
    ``MemberStreams.CONCURRENT`` CRC race.
    """

    def test_locked_with_start_does_not_call_tell(self) -> None:
        class _NoTell(io.BytesIO):
            def tell(self) -> int:
                raise AssertionError("locked SlicingStream must not tell() at init")

        lock = threading.Lock()
        underlying = _NoTell(DATA)
        sliced = SlicingStream(underlying, start=5, length=4, lock=lock)
        assert sliced.read() == DATA[5:9]

    def test_locked_without_start_tells_under_lock(self) -> None:
        lock = threading.Lock()
        held: list[bool] = []

        class _TellUnderLock(io.BytesIO):
            def tell(self) -> int:
                held.append(lock.locked())
                return super().tell()

        underlying = _TellUnderLock(DATA)
        underlying.seek(7)
        sliced = SlicingStream(underlying, length=3, lock=lock)
        assert held == [True]
        assert sliced.read() == DATA[7:10]

    def test_concurrent_locked_views_over_zipfile_fp(self, tmp_path: Path) -> None:
        """Stress concurrent locked views on stdlib ``ZipFile.fp`` (a BufferedReader)."""
        path = tmp_path / "a.zip"
        payloads = {f"f{i}.txt": f"payload-{i}".encode() * 20 for i in range(16)}
        with zipfile.ZipFile(path, "w") as zf:
            for name, data in payloads.items():
                zf.writestr(name, data)

        # Enough trials that unlocked-init construction fails reliably on this fixture.
        for _ in range(80):
            with zipfile.ZipFile(path) as zf:
                infos = [i for i in zf.infolist() if not i.is_dir()]
                barrier = threading.Barrier(len(infos))
                errors: list[BaseException] = []
                err_lock = threading.Lock()

                def worker(info: zipfile.ZipInfo) -> None:
                    try:
                        barrier.wait(timeout=5)
                        with zf._lock:
                            fp = zf.fp
                            assert fp is not None
                            saved = fp.tell()
                            try:
                                fp.seek(info.header_offset)
                                header = fp.read(30)
                                name_len = int.from_bytes(header[26:28], "little")
                                extra_len = int.from_bytes(header[28:30], "little")
                                fp.read(name_len)
                                start = info.header_offset + 30 + name_len + extra_len
                            finally:
                                fp.seek(saved)
                        # Construct outside the lock (as ZipReader does after
                        # ``_local_data_region``) — must not unlock-tell the shared fp.
                        raw = SlicingStream(
                            zf.fp,  # type: ignore[arg-type]
                            start=start,
                            length=info.compress_size,
                            lock=zf._lock,
                        )
                        compressed = raw.read()
                        data = (
                            zlib.decompress(compressed, -15)
                            if info.compress_type == zipfile.ZIP_DEFLATED
                            else compressed
                        )
                        assert data == payloads[info.filename]
                    except BaseException as exc:  # noqa: BLE001
                        with err_lock:
                            errors.append(exc)

                threads = [
                    threading.Thread(target=worker, args=(info,)) for info in infos
                ]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(timeout=30)
                if errors:
                    raise errors[0]
