"""Non-file open/read contract: directories and ANTI raise ArchiveyUsageError."""

from __future__ import annotations

import tarfile
from pathlib import Path
from zipfile import ZipFile

import pytest

from archivey import open_archive
from archivey.exceptions import ArchiveyUsageError
from archivey.types import MemberType
from tests.conftest import requires


def _assert_dir_open_raises(archive_path: Path) -> None:
    with open_archive(archive_path) as reader:
        directory = next(m for m in reader.members() if m.is_dir)
        with pytest.raises(ArchiveyUsageError, match="not a file"):
            reader.open(directory)
        with pytest.raises(ArchiveyUsageError, match="not a file"):
            reader.read(directory)
        for member, stream in reader.stream_members():
            if member.is_dir:
                assert stream is None
            elif stream is not None:
                stream.close()


def test_zip_directory_open_raises(tmp_path: Path) -> None:
    archive = tmp_path / "dirs.zip"
    with ZipFile(archive, "w") as zf:
        zf.writestr("adir/", "")
        zf.writestr("adir/f.txt", b"hi")
    _assert_dir_open_raises(archive)


def test_tar_directory_open_raises(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    (root / "adir").mkdir(parents=True)
    (root / "adir" / "f.txt").write_bytes(b"hi")
    archive = tmp_path / "dirs.tar"
    with tarfile.open(archive, "w") as tf:
        tf.add(root / "adir", arcname="adir")
    _assert_dir_open_raises(archive)


def test_directory_backend_open_raises(tmp_path: Path) -> None:
    root = tmp_path / "plain"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "f.txt").write_bytes(b"x")
    _assert_dir_open_raises(root)


@requires("pycdlib")
def test_iso_directory_open_raises(tmp_path: Path) -> None:
    from tests.test_iso import _build_iso

    path = tmp_path / "rr.iso"
    path.write_bytes(_build_iso(rock_ridge=True, joliet=True))
    with open_archive(path) as reader:
        directory = next(m for m in reader.members() if m.is_dir)
        assert directory.type is MemberType.DIRECTORY
        with pytest.raises(ArchiveyUsageError, match="not a file"):
            reader.read(directory)
