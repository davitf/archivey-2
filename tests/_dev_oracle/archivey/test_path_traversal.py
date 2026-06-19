from pathlib import Path

import pytest

from archivey import open_archive
from archivey.exceptions import (
    ArchiveMemberCannotBeOpenedError,
    ArchiveMemberNotFoundError,
)
from archivey.types import ArchiveFormat
from tests.archivey.sample_archives import (
    SAMPLE_ARCHIVES,
    SampleArchive,
    filter_archives,
)
from tests.archivey.testing_utils import skip_if_package_missing

SANITIZE_ALL_ARCHIVES = filter_archives(SAMPLE_ARCHIVES, prefixes=["sanitize"])


@pytest.mark.parametrize(
    "sample_archive", SANITIZE_ALL_ARCHIVES, ids=lambda a: a.filename
)
def test_open_symlink_outside(sample_archive: SampleArchive, sample_archive_path: str):
    """Opening a symlink that points outside the archive should fail."""
    skip_if_package_missing(sample_archive.creation_info.format, None)

    # For folder archives ensure the link target exists so failures are due to
    # the path check, not a missing file.
    if sample_archive.creation_info.format == ArchiveFormat.FOLDER:
        folder_root = Path(sample_archive_path)
        (folder_root.parent / "escape.txt").write_text("outside")

    with open_archive(sample_archive_path) as archive:
        members = {m.filename: m for m in archive.get_members()}
        member = members.get("link_outside")
        assert member is not None, "test archive missing link_outside"
        with pytest.raises(
            (ArchiveMemberCannotBeOpenedError, ArchiveMemberNotFoundError)
        ):
            archive.open(member)
