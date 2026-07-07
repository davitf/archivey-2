"""Cross-format contract for an opened member stream (``reader.open(member)``).

The per-format test files each check their backend's metadata mapping; this suite instead
asserts the *uniform* behaviour every backend's member stream must share, exercised against
a small **real** archive of each implemented format. It is the v2 stand-in for the kind of
all-formats consistency the frozen DEV oracle used to provide, scoped to the member-read
contract.

The payload is deliberately small (well under a 2 KiB ISO sector and not block-aligned), so
a backend that over-reads — e.g. an ``readinto`` that walks past the logical end into a
container's padding — is caught rather than masked by alignment. (This is exactly the
``PyCdlibIO`` EOF-misreport the ISO backend works around.)
"""

from __future__ import annotations

import gzip
import io
import tarfile
import zipfile
from pathlib import Path
from typing import Callable

import pytest

from archivey import open_archive
from tests.conftest import requires

CONTENT = b"The quick brown fox jumps over.\n"  # 32 bytes; < one ISO sector
MEMBER = "data.txt"

# A builder makes a small archive holding one ``MEMBER`` with ``CONTENT`` and returns the
# (source, member-name) pair to open. Source may be a path or directory.
Builder = Callable[[Path], tuple[Path, str]]


def _directory(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "dir"
    root.mkdir()
    (root / MEMBER).write_bytes(CONTENT)
    return root, MEMBER


def _zip_deflated(tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "a.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(MEMBER, CONTENT)
    return path, MEMBER


def _zip_stored(tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "stored.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
        z.writestr(MEMBER, CONTENT)
    return path, MEMBER


def _tar(tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "a.tar"
    with tarfile.open(path, "w") as t:
        info = tarfile.TarInfo(MEMBER)
        info.size = len(CONTENT)
        t.addfile(info, io.BytesIO(CONTENT))
    return path, MEMBER


def _tar_gz(tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "a.tar.gz"
    with tarfile.open(path, "w:gz") as t:
        info = tarfile.TarInfo(MEMBER)
        info.size = len(CONTENT)
        t.addfile(info, io.BytesIO(CONTENT))
    return path, MEMBER


def _gzip(tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "data.txt.gz"
    with gzip.open(path, "wb") as f:
        f.write(CONTENT)
    return path, MEMBER  # single-file member name inferred from the source filename


def _iso(tmp_path: Path) -> tuple[Path, str]:
    import pycdlib

    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3, rock_ridge="1.09")
    iso.add_fp(io.BytesIO(CONTENT), len(CONTENT), "/DATA.TXT;1", rr_name=MEMBER)
    path = tmp_path / "a.iso"
    iso.write(str(path))
    iso.close()
    return path, MEMBER


@pytest.fixture(
    params=[
        pytest.param(_directory, id="directory"),
        pytest.param(_zip_deflated, id="zip_deflated"),
        pytest.param(_zip_stored, id="zip_stored"),
        pytest.param(_tar, id="tar"),
        pytest.param(_tar_gz, id="tar_gz"),
        pytest.param(_gzip, id="gzip"),
        pytest.param(_iso, id="iso", marks=requires("pycdlib")),
    ]
)
def member(request: pytest.FixtureRequest, tmp_path: Path) -> tuple[Path, str]:
    builder: Builder = request.param
    return builder(tmp_path)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def test_read_more_than_available_returns_all(member: tuple[Path, str]) -> None:
    source, name = member
    with open_archive(source) as ar, ar.open(name) as f:
        # A read size far beyond the member length returns exactly the member's bytes.
        assert f.read(10_000) == CONTENT


def test_read_at_eof_returns_empty(member: tuple[Path, str]) -> None:
    source, name = member
    with open_archive(source) as ar, ar.open(name) as f:
        assert f.read() == CONTENT
        assert f.read() == b""
        assert f.read(64) == b""


def test_readinto_oversized_buffer_truncates_at_eof(member: tuple[Path, str]) -> None:
    # readinto into a buffer larger than the remaining data must return the actual byte
    # count (not the buffer size) and fill only those bytes — never reading into a
    # container's sector/block padding past the member's logical end.
    source, name = member
    with open_archive(source) as ar, ar.open(name) as f:
        buf = bytearray(10_000)
        n = f.readinto(buf)
        assert n == len(CONTENT)
        assert bytes(buf[:n]) == CONTENT
        # A second readinto at EOF fills nothing.
        assert f.readinto(bytearray(64)) == 0


def test_piecewise_read_then_eof(member: tuple[Path, str]) -> None:
    source, name = member
    with open_archive(source) as ar, ar.open(name) as f:
        first = f.read(5)
        assert first == CONTENT[:5]
        assert first + f.read() == CONTENT
        assert f.read(1) == b""


# ---------------------------------------------------------------------------
# Seeking (only when the member stream reports seekable)
# ---------------------------------------------------------------------------


def test_seek_past_end_then_read_returns_empty(member: tuple[Path, str]) -> None:
    source, name = member
    with open_archive(source) as ar, ar.open(name) as f:
        if not f.seekable():
            pytest.skip("member stream is not seekable")
        f.seek(len(CONTENT) + 100)
        assert f.read() == b""
        assert f.read(64) == b""


def test_seek_to_start_rereads(member: tuple[Path, str]) -> None:
    source, name = member
    with open_archive(source) as ar, ar.open(name) as f:
        if not f.seekable():
            pytest.skip("member stream is not seekable")
        assert f.read(5) == CONTENT[:5]
        f.seek(0)
        assert f.read() == CONTENT


# ---------------------------------------------------------------------------
# Uniform handle type
# ---------------------------------------------------------------------------


def test_member_streams_are_archive_streams(member: tuple[Path, str]) -> None:
    # Every member handle the library hands out — from open() and from
    # stream_members() alike — is an ArchiveStream, regardless of backend (even the
    # directory backend, which has nothing to decompress): uniform error
    # translation/stamping, the `size` advertisement, and one place to grow shared
    # handle features.
    from archivey.internal.streams.archive_stream import ArchiveStream

    source, name = member
    with open_archive(source) as ar:
        with ar.open(name) as f:
            assert isinstance(f, ArchiveStream)
        for m, stream in ar.stream_members():
            if m.is_file:
                assert isinstance(stream, ArchiveStream)
            if stream is not None:
                stream.close()


def test_member_stream_advertises_size(member: tuple[Path, str]) -> None:
    # The fsspec-style `size` attribute carries the decompressed length when the
    # archive metadata knows it (feeds nested-archive sizing and the bomb tracker).
    source, name = member
    with open_archive(source) as ar, ar.open(name) as f:
        size = getattr(f, "size", None)
        assert size is None or size == len(CONTENT)
