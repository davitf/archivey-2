import pytest
from archivey.config import ArchiveyConfig
from archivey.core import open_archive
from tests.archivey.sample_archives import (
    BASIC_ARCHIVES,
    HARDLINK_ARCHIVES,
    SINGLE_FILE_ARCHIVES,
    SYMLINK_ARCHIVES,
    SampleArchive,
)
from tests.archivey.testing_utils import remove_duplicate_files


@pytest.mark.sample_archives(
    archives=BASIC_ARCHIVES + SYMLINK_ARCHIVES + HARDLINK_ARCHIVES,
    configs=["default", "altlibs"],
)
def test_open_member(
    sample_archive: SampleArchive,
    sample_archive_path: str,
    archivey_config: ArchiveyConfig | None,
):
    config = archivey_config or ArchiveyConfig()
    with open_archive(sample_archive_path, config=config) as archive:
        for sample_file in remove_duplicate_files(sample_archive.contents.files):
            if sample_file.contents is not None:
                stream = archive.open(sample_file.name)
                data = stream.read()
                assert data == sample_file.contents


@pytest.mark.sample_archives(
    archives=SINGLE_FILE_ARCHIVES,
    configs=["default", "altlibs"],
)
def test_open_member_single_file_archives(
    sample_archive: SampleArchive,
    sample_archive_path: str,
    archivey_config: ArchiveyConfig | None,
):
    config = archivey_config or ArchiveyConfig()
    with open_archive(sample_archive_path, config=config) as archive:
        member = archive.get_members()[0]

        stream = archive.open(member)
        data = stream.read()
        assert data == sample_archive.contents.files[0].contents
