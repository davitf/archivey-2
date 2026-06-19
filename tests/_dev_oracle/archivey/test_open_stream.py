import io

import pytest

from archivey.core import open_archive
from archivey.types import ArchiveFormat
from tests.archivey.sample_archives import ALTERNATIVE_CONFIG, SAMPLE_ARCHIVES
from tests.archivey.testing_utils import skip_if_package_missing

# Select one sample archive for each format (except FOLDER and ISO)
archives_by_format = {}
for a in SAMPLE_ARCHIVES:
    fmt = a.creation_info.format
    if fmt in (ArchiveFormat.FOLDER, ArchiveFormat.ISO):
        continue
    archives_by_format.setdefault(fmt, a)


@pytest.mark.parametrize(
    "sample_archive", list(archives_by_format.values()), ids=lambda a: a.filename
)
@pytest.mark.parametrize(
    "alternative_packages", [False, True], ids=["defaultlibs", "altlibs"]
)
def test_open_stream(sample_archive, alternative_packages):
    config = ALTERNATIVE_CONFIG if alternative_packages else None
    skip_if_package_missing(sample_archive.creation_info.format, config)

    path = sample_archive.get_archive_path()
    with open(path, "rb") as f:
        data = f.read()

    with open_archive(io.BytesIO(data), config=config) as archive:
        has_member = False
        for member, stream in archive.iter_members_with_streams():
            has_member = True
            if stream is not None:
                stream.read()
        assert has_member
