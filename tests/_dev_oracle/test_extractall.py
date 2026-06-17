import logging
import os
from datetime import datetime
from pathlib import Path

import pytest
from archivey.core import open_archive
from archivey.internal.utils import (
    ensure_not_none,
    platform_supports_setting_symlink_mtime,
    platform_supports_setting_symlink_permissions,
)
from archivey.types import ArchiveMember, MemberType
from tests.archivey.sample_archives import (
    BASIC_ARCHIVES,
    DUPLICATE_FILES_ARCHIVES,
    HARDLINK_ARCHIVES,
    SYMLINK_ARCHIVES,
    FileInfo,
    SampleArchive,
)
from tests.archivey.testing_utils import remove_duplicate_files

logger = logging.getLogger(__name__)


def _check_file_metadata(path: Path, info: FileInfo, sample: SampleArchive):
    stat = path.lstat() if info.type == MemberType.SYMLINK else path.stat()
    features = sample.creation_info.features

    if (
        info.type == MemberType.SYMLINK
        and not platform_supports_setting_symlink_permissions()
    ):
        pass
    elif info.permissions is not None:
        assert (stat.st_mode & 0o777) == info.permissions, path

    # Extracted hardlinks will have the same mtime as the original file, which can be
    # different from the mtime in the archive.
    if not features.mtime or info.type == MemberType.HARDLINK:
        return

    actual = datetime.fromtimestamp(stat.st_mtime)
    if (
        info.type == MemberType.SYMLINK
        and not platform_supports_setting_symlink_mtime()
    ):
        pass
    elif features.rounded_mtime:
        assert abs(actual.timestamp() - info.mtime.timestamp()) <= 1, path
    else:
        assert actual == info.mtime, (path, info)


@pytest.mark.sample_archives(
    archives=BASIC_ARCHIVES + DUPLICATE_FILES_ARCHIVES + SYMLINK_ARCHIVES + HARDLINK_ARCHIVES
)
def test_extractall(
    tmp_path: Path, sample_archive: SampleArchive, sample_archive_path: str
):
    dest = tmp_path / "out"
    dest.mkdir()

    logger.info(f"Extracting {sample_archive_path} to {dest}")

    with open_archive(sample_archive_path) as archive:
        extractall_result = archive.extractall(dest)
        members_by_filename = {
            m.filename: m for m in ensure_not_none(archive.get_members_if_available())
        }

    expected_files: set[str] = set()
    implicit_dirs: set[str] = set()

    expected_extractall_result: dict[str, ArchiveMember] = {}

    for info in remove_duplicate_files(sample_archive.contents.files):
        path = dest / info.name.rstrip("/")

        # Broken hardlink targets should not be extracted.
        if info.type != MemberType.HARDLINK or info.contents is not None:
            assert os.path.lexists(path), f"Missing {path}"
            expected_files.add(str(path.relative_to(dest)).replace(os.sep, "/"))

            # Add any implicit parent directories.
            dirname = os.path.dirname(info.name)
            while dirname:
                implicit_dirs.add(dirname)
                dirname = os.path.dirname(dirname)

        else:
            # Broken hardlinks should not exist at all in the extracted folder.
            assert not os.path.lexists(path), f"Broken hardlink {path} should not exist"
            continue

        if info.type == MemberType.DIR:
            assert path.is_dir()

        elif info.type == MemberType.SYMLINK:
            assert path.is_symlink()
            assert info.link_target is not None
            # When extracting links to dirs, the link target may not include the trailing slash.
            assert os.readlink(path).removesuffix("/") == info.link_target.removesuffix(
                "/"
            )
        else:
            assert path.is_file()
            with open(path, "rb") as f:
                assert f.read() == (info.contents or b"")

        _check_file_metadata(path, info, sample_archive)
        # if info.type != MemberType.HARDLINK or info.name != info.link_target:
        expected_extractall_result[str(path)] = members_by_filename[info.name]

    implicit_dirs -= expected_files
    # Check that no extra files were extracted.
    extracted = {str(p.relative_to(dest)).replace(os.sep, "/") for p in dest.rglob("*")}
    assert (expected_files | implicit_dirs) == extracted

    # Some sample archives may include the implicit parent directories, which are
    # not in sample_archive.contents.files.
    for dirname in implicit_dirs:
        extractall_result.pop(str(dest / dirname), None)

    # Check that the dict returned by extractall is correct.
    assert expected_extractall_result == extractall_result


@pytest.mark.sample_archives(archives=BASIC_ARCHIVES)
def test_extractall_filter(
    tmp_path: Path, sample_archive: SampleArchive, sample_archive_path: str
):
    dest = tmp_path / "out"
    dest.mkdir()

    with open_archive(sample_archive_path) as archive:
        archive.extractall(dest, members=lambda m: m.filename.endswith("file2.txt"))

    path = dest / "subdir" / "file2.txt"
    assert path.exists() and path.is_file()
    info = next(
        f for f in sample_archive.contents.files if f.name == "subdir/file2.txt"
    )
    with open(path, "rb") as f:
        assert f.read() == (info.contents or b"")
    _check_file_metadata(path, info, sample_archive)

    assert not (dest / "file1.txt").exists()
    assert not (dest / "implicit_subdir" / "file3.txt").exists()


@pytest.mark.sample_archives(archives=BASIC_ARCHIVES)
def test_extractall_members(
    tmp_path: Path, sample_archive: SampleArchive, sample_archive_path: str
):
    dest = tmp_path / "out"
    dest.mkdir()

    with open_archive(sample_archive_path) as archive:
        member_obj = archive.get_member("file1.txt")
        archive.extractall(dest, members=[member_obj, "subdir/file2.txt"])

    expected_paths = [dest / "file1.txt", dest / "subdir" / "file2.txt"]
    for p in expected_paths:
        assert p.exists() and p.is_file()
        info = next(
            f
            for f in sample_archive.contents.files
            if f.name == str(p.relative_to(dest)).replace(os.sep, "/")
        )
        with open(p, "rb") as f:
            assert f.read() == (info.contents or b"")
        _check_file_metadata(p, info, sample_archive)

    assert not (dest / "implicit_subdir" / "file3.txt").exists()
