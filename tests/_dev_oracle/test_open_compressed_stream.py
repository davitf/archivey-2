import io
import logging

import pytest
from archivey.config import ArchiveyConfig
from archivey.core import open_compressed_stream
from archivey.exceptions import ArchiveNotSupportedError
from archivey.types import ContainerFormat, StreamFormat
from tests.archivey.sample_archives import (
    BASIC_ARCHIVES,
    SINGLE_FILE_ARCHIVES,
    filter_archives,
)

BASIC_ZIP_ARCHIVE = filter_archives(BASIC_ARCHIVES, extensions=["zip"])[0]

logger = logging.getLogger(__name__)


@pytest.mark.sample_archives(archives=SINGLE_FILE_ARCHIVES, configs=["default", "altlibs"])
def test_open_compressed_stream_from_file(
    sample_archive, sample_archive_path, archivey_config: ArchiveyConfig | None
):
    config = archivey_config or ArchiveyConfig()
    with open_compressed_stream(sample_archive_path, config=config) as f:
        data = f.read()

    expected = sample_archive.contents.files[0].contents
    assert data == expected


def test_open_compressed_stream_unsupported_format(tmp_path):
    sample_archive = BASIC_ZIP_ARCHIVE
    path = sample_archive.get_archive_path()
    with pytest.raises(ArchiveNotSupportedError):
        open_compressed_stream(path)


@pytest.mark.sample_archives(archives=SINGLE_FILE_ARCHIVES, configs=["default", "altlibs"])
def test_open_compressed_stream_from_stream(
    sample_archive, sample_archive_path, archivey_config: ArchiveyConfig | None
):
    config = archivey_config or ArchiveyConfig()
    compressed_data = open(sample_archive_path, "rb").read()
    compressed_stream = io.BytesIO(compressed_data)

    with open_compressed_stream(compressed_stream, config=config) as f:
        data = f.read()

    expected = sample_archive.contents.files[0].contents
    assert data == expected


@pytest.mark.sample_archives(archives=SINGLE_FILE_ARCHIVES, configs=["default", "altlibs"])
def test_open_compressed_stream_from_stream_with_prefix(
    sample_archive, sample_archive_path, archivey_config: ArchiveyConfig | None
):
    config = archivey_config or ArchiveyConfig()
    bad_data = b"bad data " * 1000
    compressed_data = bad_data + open(sample_archive_path, "rb").read()
    compressed_stream = io.BytesIO(compressed_data)
    compressed_stream.seek(len(bad_data))

    with open_compressed_stream(compressed_stream, config=config) as f:
        data = f.read()

    expected = sample_archive.contents.files[0].contents
    assert data == expected


@pytest.mark.sample_archives(archives=BASIC_ARCHIVES, configs=["default", "altlibs"])
def test_open_compressed_stream_from_archive(
    sample_archive, sample_archive_path, archivey_config: ArchiveyConfig | None
):
    config = archivey_config or ArchiveyConfig()

    if (
        sample_archive.creation_info.format.container == ContainerFormat.TAR
        and sample_archive.creation_info.format.stream != StreamFormat.UNCOMPRESSED
    ):
        with open_compressed_stream(sample_archive_path, config=config) as f:
            data = f.read()
            assert data[257 : 257 + 5] == b"ustar"
    else:
        with pytest.raises(ArchiveNotSupportedError):
            open_compressed_stream(sample_archive_path, config=config)
