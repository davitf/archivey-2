"""Multi-thread stress for promoted MemberStreams.CONCURRENT.

These tests also run under the Linux ``3.13t`` ``free-threaded-concurrency`` CI job
(marker ``concurrent_reader``). Optional backends (ISO/pycdlib) skip cleanly and are
not claimed covered by the core-only free-threaded job.
"""

from __future__ import annotations

import gzip
import io
import tarfile
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

from archivey import MemberStreams, open_archive
from archivey.exceptions import ArchiveyUsageError

pytestmark = pytest.mark.concurrent_reader


def _dir_with_files(tmp_path: Path, n: int = 8) -> Path:
    root = tmp_path / "dir"
    root.mkdir()
    for i in range(n):
        (root / f"f{i}.txt").write_bytes(f"payload-{i}".encode() * 20)
    return root


def _make_zip(path: Path, n: int = 8) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for i in range(n):
            zf.writestr(f"f{i}.txt", f"payload-{i}".encode() * 20)
    return path


def _make_tar(path: Path, *, compressed: bool = False, n: int = 8) -> Path:
    mode = "w:gz" if compressed else "w"
    with tarfile.open(path, mode) as tf:
        for i in range(n):
            data = f"payload-{i}".encode() * 20
            info = tarfile.TarInfo(name=f"f{i}.txt")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


def _expected(n: int = 8) -> dict[str, bytes]:
    return {f"f{i}.txt": f"payload-{i}".encode() * 20 for i in range(n)}


def _fan_out_read(path: Path, *, seekable: bool = False) -> dict[str, bytes]:
    flags = MemberStreams.CONCURRENT
    if seekable:
        flags |= MemberStreams.SEEKABLE
    with open_archive(path, member_streams=flags) as reader:
        members = [m for m in reader.members() if m.is_file]
        barrier = threading.Barrier(len(members))
        got: dict[str, bytes] = {}
        lock = threading.Lock()
        errors: list[BaseException] = []

        def worker(name: str) -> None:
            try:
                barrier.wait(timeout=5)
                with reader.open(name) as stream:
                    data = stream.read()
                with lock:
                    got[name] = data
            except BaseException as exc:  # noqa: BLE001 - collect and re-raise below
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker, args=(m.name,)) for m in members]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
            assert not t.is_alive()
        if errors:
            raise errors[0]
        return got


@pytest.mark.concurrent_reader
def test_multithread_directory_open_read(tmp_path: Path) -> None:
    root = _dir_with_files(tmp_path)
    assert _fan_out_read(root) == _expected()


@pytest.mark.concurrent_reader
def test_multithread_zip_open_read(tmp_path: Path) -> None:
    path = _make_zip(tmp_path / "a.zip")
    assert _fan_out_read(path) == _expected()


@pytest.mark.concurrent_reader
def test_multithread_plain_tar_open_read(tmp_path: Path) -> None:
    path = _make_tar(tmp_path / "a.tar")
    assert _fan_out_read(path) == _expected()


@pytest.mark.concurrent_reader
def test_multithread_gzip_tar_open_read(tmp_path: Path) -> None:
    path = _make_tar(tmp_path / "a.tar.gz", compressed=True)
    assert _fan_out_read(path) == _expected()


@pytest.mark.concurrent_reader
def test_multithread_single_file_gz(tmp_path: Path) -> None:
    # Single-file archives have one member; stress overlapping open after materialization
    # is not applicable — instead fan reads across two concurrent opens of the same
    # archive path (two readers), and one CONCURRENT reader with SEEKABLE chunk reads.
    raw = b"hello-single-file-payload" * 50
    path = tmp_path / "x.gz"
    path.write_bytes(gzip.compress(raw))
    with open_archive(
        path, member_streams=MemberStreams.CONCURRENT | MemberStreams.SEEKABLE
    ) as reader:
        reader.members()
        member = next(m for m in reader.members() if m.is_file)

        def read_all() -> bytes:
            with reader.open(member) as stream:
                return stream.read()

        with ThreadPoolExecutor(max_workers=2) as pool:
            # Two overlapping opens of the same member are allowed under CONCURRENT;
            # each must see the full payload.
            futs = [pool.submit(read_all), pool.submit(read_all)]
            results = [f.result(timeout=30) for f in as_completed(futs)]
        assert results == [raw, raw]


def test_multithread_iso_open_read(tmp_path: Path) -> None:
    pytest.importorskip("pycdlib")
    # Build a tiny ISO via pycdlib.
    import pycdlib

    iso_path = tmp_path / "a.iso"
    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=1)
    for i in range(4):
        name = f"F{i}.TXT;1"
        data = f"payload-{i}".encode() * 20
        iso.add_fp(io.BytesIO(data), len(data), f"/{name}")
    iso.write(str(iso_path))
    iso.close()

    expected = {f"F{i}.TXT": f"payload-{i}".encode() * 20 for i in range(4)}
    # ISO member names may normalize; compare by payload set via open-by-member.
    with open_archive(iso_path, member_streams=MemberStreams.CONCURRENT) as reader:
        files = [m for m in reader.members() if m.is_file]
        assert len(files) >= 4
        barrier = threading.Barrier(len(files))
        got: dict[str, bytes] = {}
        lock = threading.Lock()

        def worker(member) -> None:
            barrier.wait(timeout=5)
            with reader.open(member) as stream:
                data = stream.read()
            with lock:
                got[member.name] = data

        threads = [threading.Thread(target=worker, args=(m,)) for m in files]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
            assert not t.is_alive()
        assert set(got.values()) == set(expected.values())


@pytest.mark.concurrent_reader
def test_close_with_live_streams_defers_teardown(tmp_path: Path) -> None:
    """Live streams survive reader.close(); new opens fail; bytes remain readable."""
    path = _make_zip(tmp_path / "a.zip", n=4)
    reader = open_archive(path, member_streams=MemberStreams.CONCURRENT)
    members = [m for m in reader.members() if m.is_file]
    streams = [reader.open(m.name) for m in members[:2]]
    reader.close()
    with pytest.raises(ArchiveyUsageError, match="closed"):
        reader.open(members[2].name)
    assert {s.read() for s in streams} == {
        _expected(4)[members[0].name],
        _expected(4)[members[1].name],
    }
    for s in streams:
        s.close()


@pytest.mark.concurrent_reader
def test_multithread_open_rejected_during_stream_members(tmp_path: Path) -> None:
    path = _make_zip(tmp_path / "a.zip", n=4)
    with open_archive(path, member_streams=MemberStreams.CONCURRENT) as reader:
        it = reader.stream_members()
        next(it)
        errors: list[BaseException] = []

        def try_open() -> None:
            try:
                reader.open("f0.txt")
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=try_open) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert errors and all(isinstance(e, ArchiveyUsageError) for e in errors)
        list(it)
