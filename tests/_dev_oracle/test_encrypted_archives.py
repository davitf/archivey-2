from pathlib import Path

import pytest
from archivey.core import open_archive
from archivey.exceptions import ArchiveEncryptedError, ArchiveError
from archivey.internal.utils import platform_is_windows
from archivey.types import ArchiveFormat, MemberType
from tests.archivey.sample_archives import (
    SAMPLE_ARCHIVES,
    SampleArchive,
    filter_archives,
)

# Select encrypted sample archives that use a single password and no header password
ENCRYPTED_ARCHIVES = filter_archives(
    SAMPLE_ARCHIVES,
    prefixes=[
        "encryption",
        "encryption_with_plain",
        "encryption_solid",
        "encryption_with_symlinks",
    ],
    extensions=["zip", "rar", "7z"],
    custom_filter=lambda a: (
        not a.contents.has_multiple_passwords() and a.contents.header_password is None
    ),
)


def _archive_password(sample: SampleArchive) -> str:
    for f in sample.contents.files:
        if f.password is not None:
            return f.password
    raise ValueError("sample archive has no password")


def _first_encrypted_file(sample: SampleArchive):
    for f in sample.contents.files:
        if f.password is not None and f.type == MemberType.FILE:
            return f
    raise ValueError("sample archive has no encrypted files")


@pytest.mark.sample_archives(archives=ENCRYPTED_ARCHIVES)
def test_password_in_open_archive(
    sample_archive: SampleArchive, sample_archive_path: str
):
    pwd = _archive_password(sample_archive)
    with open_archive(sample_archive_path, pwd=pwd) as archive:
        encrypted = _first_encrypted_file(sample_archive)
        with archive.open(encrypted.name) as fh:
            assert fh.read() == encrypted.contents


@pytest.mark.sample_archives(archives=ENCRYPTED_ARCHIVES)
def test_password_in_iter_members(
    sample_archive: SampleArchive, sample_archive_path: str
):
    pwd = _archive_password(sample_archive)
    with open_archive(sample_archive_path) as archive:
        if sample_archive.creation_info.format == ArchiveFormat.SEVENZIP:
            pytest.skip(
                "py7zr does not support password parameter for iter_members_with_streams"
            )
        contents = {}
        for m, stream in archive.iter_members_with_streams(pwd=pwd):
            if m.is_file:
                assert stream is not None
                contents[m.filename] = stream.read()
        for f in sample_archive.contents.files:
            if f.type == MemberType.FILE:
                assert contents[f.name] == f.contents


@pytest.mark.sample_archives(archives=ENCRYPTED_ARCHIVES)
def test_password_in_open(sample_archive: SampleArchive, sample_archive_path: str):
    pwd = _archive_password(sample_archive)
    with open_archive(sample_archive_path) as archive:
        for f in sample_archive.contents.files:
            if f.type == MemberType.FILE:
                with archive.open(f.name, pwd=pwd) as fh:
                    assert fh.read() == f.contents


@pytest.mark.sample_archives(archives=ENCRYPTED_ARCHIVES)
def test_wrong_password_open(sample_archive: SampleArchive, sample_archive_path: str):
    wrong = "wrong_password"
    encrypted = _first_encrypted_file(sample_archive)
    with open_archive(sample_archive_path) as archive:
        with pytest.raises((ArchiveEncryptedError, ArchiveError)):
            with archive.open(encrypted.name, pwd=wrong) as f:
                f.read()


@pytest.mark.sample_archives(archives=ENCRYPTED_ARCHIVES)
def test_wrong_password_iter_members_read(
    sample_archive: SampleArchive, sample_archive_path: str
):
    if sample_archive.creation_info.format == ArchiveFormat.SEVENZIP:
        pytest.skip(
            "py7zr does not support password parameter for iter_members_with_streams"
        )

    wrong = "wrong_password"
    with open_archive(sample_archive_path) as archive:
        for m, stream in archive.iter_members_with_streams(pwd=wrong):
            assert stream is not None
            if m.is_file:
                if m.encrypted:
                    with pytest.raises((ArchiveEncryptedError, ArchiveError)):
                        stream.read()
                else:
                    stream.read()


@pytest.mark.sample_archives(archives=ENCRYPTED_ARCHIVES)
def test_wrong_password_iter_members_no_read(
    sample_archive: SampleArchive, sample_archive_path: str
):
    wrong = "wrong_password"
    with open_archive(sample_archive_path) as archive:
        if sample_archive.creation_info.format == ArchiveFormat.SEVENZIP:
            pytest.skip(
                "py7zr does not support password parameter for iter_members_with_streams"
            )
        for _m, _stream in archive.iter_members_with_streams(pwd=wrong):
            pass


@pytest.mark.sample_archives(archives=ENCRYPTED_ARCHIVES)
def test_extract_with_password(
    tmp_path: Path, sample_archive: SampleArchive, sample_archive_path: str
):
    pwd = _archive_password(sample_archive)
    dest = tmp_path / "out"
    dest.mkdir()
    encrypted = _first_encrypted_file(sample_archive)
    # config = get_default_config()
    with open_archive(sample_archive_path) as archive:
        if sample_archive.creation_info.format == ArchiveFormat.SEVENZIP:
            pytest.skip("py7zr extract password support incomplete")
        # archive.config.overwrite_mode = OverwriteMode.OVERWRITE
        path = archive.extract(encrypted.name, dest, pwd=pwd)
    extracted_path = Path(path or dest / encrypted.name)
    with open(extracted_path, "rb") as f:
        assert f.read() == encrypted.contents


@pytest.mark.sample_archives(archives=ENCRYPTED_ARCHIVES)
def test_extractall_with_password(
    tmp_path: Path, sample_archive: SampleArchive, sample_archive_path: str
):
    # if sample_archive.creation_info.format == ArchiveFormat.SEVENZIP:
    #     pytest.skip("py7zr extractall password support incomplete")

    pwd = _archive_password(sample_archive)
    dest = tmp_path / "all"
    dest.mkdir()
    with open_archive(sample_archive_path) as archive:
        archive.extractall(dest, pwd=pwd)

    for f in sample_archive.contents.files:
        if f.type == MemberType.FILE:
            path = dest / f.name
            assert path.exists()
            with open(path, "rb") as fh:
                assert fh.read() == f.contents


@pytest.mark.sample_archives(archives=ENCRYPTED_ARCHIVES)
def test_extract_wrong_password(
    tmp_path: Path, sample_archive: SampleArchive, sample_archive_path: str
):
    wrong = "wrong_password"
    dest = tmp_path / "out"
    dest.mkdir()
    encrypted = _first_encrypted_file(sample_archive)
    with open_archive(sample_archive_path) as archive:
        if sample_archive.creation_info.format == ArchiveFormat.SEVENZIP:
            pytest.skip("py7zr extract password support incomplete")
        # archive.config.overwrite_mode = OverwriteMode.OVERWRITE
        with pytest.raises((ArchiveEncryptedError, ArchiveError)):
            archive.extract(encrypted.name, dest, pwd=wrong)


@pytest.mark.sample_archives(archives=ENCRYPTED_ARCHIVES)
def test_extractall_wrong_password(
    tmp_path: Path, sample_archive: SampleArchive, sample_archive_path: str
):
    # if sample_archive.creation_info.format == ArchiveFormat.SEVENZIP:
    #     pytest.skip("py7zr extractall password support incomplete")

    wrong = "wrong_password"
    dest = tmp_path / "all"
    dest.mkdir()
    with open_archive(sample_archive_path) as archive:
        with pytest.raises((ArchiveEncryptedError, ArchiveError)):
            archive.extractall(dest, pwd=wrong)


# @pytest.mark.parametrize("sample_archive", ENCRYPTED_ARCHIVES, ids=lambda a: a.filename)
@pytest.mark.sample_archives(prefixes=["encryption_with_symlinks"])
def test_iterator_encryption_with_symlinks_no_password(
    sample_archive: SampleArchive, sample_archive_path: str
):
    members_by_name = {}
    with open_archive(sample_archive_path) as archive:
        for member, stream in archive.iter_members_with_streams():
            members_by_name[member.filename] = stream

    assert set(members_by_name.keys()) == {
        f.name for f in sample_archive.contents.files
    }


@pytest.mark.sample_archives(prefixes=["encryption_with_symlinks"])
def test_iterator_encryption_with_symlinks_password_in_open_archive(
    sample_archive: SampleArchive, sample_archive_path: str
):
    members_by_name = {}
    with open_archive(sample_archive_path, pwd="pwd") as archive:
        for member, stream in archive.iter_members_with_streams():
            members_by_name[member.filename] = stream

    assert set(members_by_name.keys()) == {
        f.name for f in sample_archive.contents.files
    }


@pytest.mark.sample_archives(prefixes=["encryption_with_symlinks"])
def test_iterator_encryption_with_symlinks_password_in_iterator(
    sample_archive: SampleArchive, sample_archive_path: str
):
    members_by_name = {}
    with open_archive(sample_archive_path) as archive:
        for member, stream in archive.iter_members_with_streams(pwd="pwd"):
            members_by_name[member.filename] = stream

    assert set(members_by_name.keys()) == {
        f.name for f in sample_archive.contents.files
    }


@pytest.mark.sample_archives(
    prefixes=["encryption_with_symlinks"],
    extensions=["rar", "7z"],
)
def test_open_encrypted_symlink(
    sample_archive: SampleArchive, sample_archive_path: str
):
    sample_files = {f.name: f for f in sample_archive.contents.files}

    files_to_test = [
        ("encrypted_link_to_secret.txt", "pwd"),
        ("encrypted_link_to_not_secret.txt", "longpwd"),
        ("plain_link_to_secret.txt", "pwd"),
    ]
    with open_archive(sample_archive_path) as archive:
        for filename, pwd in files_to_test:
            try:
                data = archive.open(filename, pwd=pwd).read()
                assert data == sample_files[filename].contents

                # After reading the file, the link target should have been set
                member = archive.get_member(filename)
                assert member.link_target == sample_files[filename].link_target
            except ArchiveEncryptedError:
                # The workaround we have to read encrypted symlink targets for RAR4
                # archives involves extracting the symlink and reading the target,
                # which doesn't fully work on Windows.
                if (
                    platform_is_windows()
                    and filename.startswith("encrypted_link_to_")
                    and sample_archive_path.endswith("rar4.rar")
                ):
                    pytest.xfail("Windows does not support encrypted symlinks")
                else:
                    raise


@pytest.mark.sample_archives(
    prefixes=["encryption_with_symlinks"],
    extensions=["rar", "7z"],
)
def test_open_encrypted_symlink_wrong_password(
    sample_archive: SampleArchive, sample_archive_path: str
):
    symlink_name = "encrypted_link_to_secret.txt"

    with open_archive(sample_archive_path) as archive:
        with pytest.raises((ArchiveEncryptedError, ArchiveError)):
            with archive.open(symlink_name, pwd="wrong") as fh:
                fh.read()


@pytest.mark.sample_archives(
    prefixes=["encryption_with_symlinks"],
    extensions=["rar", "7z"],
)
def test_open_encrypted_symlink_target_wrong_password(
    sample_archive: SampleArchive, sample_archive_path: str
):
    symlink_name = "encrypted_link_to_very_secret.txt"

    with open_archive(sample_archive_path) as archive:
        with pytest.raises((ArchiveEncryptedError, ArchiveError)):
            with archive.open(symlink_name, pwd="pwd") as fh:
                fh.read()
