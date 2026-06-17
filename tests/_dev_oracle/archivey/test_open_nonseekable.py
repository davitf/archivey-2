import io
import logging

import pytest

from archivey.config import ArchiveyConfig
from archivey.core import open_archive, open_compressed_stream
from archivey.exceptions import ArchiveStreamNotSeekableError
from archivey.internal.io_helpers import ensure_binaryio
from archivey.types import ArchiveFormat
from tests.archivey.sample_archives import (
    BASIC_ARCHIVES,
    LARGE_ARCHIVES,
    SINGLE_FILE_ARCHIVES,
    SampleArchive,
    filter_archives,
)

logger = logging.getLogger(__name__)

# Formats known to fail when opened from a non-seekable stream
SKIPPABLE_FORMATS: set[ArchiveFormat] = {
    ArchiveFormat.ZIP,
    ArchiveFormat.RAR,
    ArchiveFormat.SEVENZIP,
}


# Formats known to fail when opened from a non-seekable stream with default/alternative packages
EXPECTED_NON_SEEKABLE_FAILURES: set[tuple[ArchiveFormat, bool]] = {
    (ArchiveFormat.GZIP, True),
    (ArchiveFormat.BZIP2, True),
    # XZ uses python-xz under ALTERNATIVE_CONFIG (use_python_xz=True), which
    # requires a seekable stream. The default backend (XzDecompressorStream)
    # supports non-seekable streams, so only the altlibs (True) variant fails.
    (ArchiveFormat.XZ, True),
    (ArchiveFormat.TAR_GZ, True),
    (ArchiveFormat.TAR_BZ2, True),
    (ArchiveFormat.TAR_XZ, True),
    (ArchiveFormat.TAR_Z, False),
    (ArchiveFormat.TAR_Z, True),
    (ArchiveFormat.ZIP, False),
    (ArchiveFormat.ZIP, True),
    (ArchiveFormat.RAR, False),
    (ArchiveFormat.RAR, True),
    (ArchiveFormat.SEVENZIP, False),
    (ArchiveFormat.SEVENZIP, True),
    (ArchiveFormat.UNIX_COMPRESS, False),
    (ArchiveFormat.UNIX_COMPRESS, True),
}


class NonSeekableBytesIO(io.BytesIO):
    def seekable(self) -> bool:  # pragma: no cover - simple
        return False

    def seek(self, *args, **kwargs):  # pragma: no cover - simple
        raise io.UnsupportedOperation("seek")

    def tell(self, *args, **kwargs):  # pragma: no cover - simple
        raise io.UnsupportedOperation("tell")


@pytest.mark.sample_archives(
    archives=filter_archives(
        BASIC_ARCHIVES + LARGE_ARCHIVES,
        custom_filter=lambda a: a.creation_info.format not in (ArchiveFormat.FOLDER,),
    ),
    configs=["default", "altlibs"],
)
def test_open_archive_nonseekable(
    sample_archive: SampleArchive,
    sample_archive_path: str,
    archivey_config: ArchiveyConfig | None,
):
    """Ensure open_archive can read from non-seekable streams in streaming mode."""
    alternative_packages = archivey_config is not None
    config = archivey_config

    with open(sample_archive_path, "rb") as f:
        data = f.read()

    stream = NonSeekableBytesIO(data)

    try:
        with open_archive(stream, streaming_only=True, config=config) as archive:
            members = []
            for member, member_stream in archive.iter_members_with_streams():
                members.append(member)
                if member_stream is not None:
                    member_stream.read()
            # The file names may not match in case of single-file archives, as we take
            # the name from the compressed file name which is not available when reading
            # from a stream. But checking the number of members should ensure that
            # we read all the members.
            assert len(members) == len([f.name for f in sample_archive.contents.files])

    except (
        ArchiveStreamNotSeekableError
    ) as exc:  # pragma: no cover - environment dependent
        key = (sample_archive.creation_info.format, alternative_packages)
        if key in EXPECTED_NON_SEEKABLE_FAILURES:
            pytest.xfail(
                f"Non-seekable {sample_archive.creation_info.format} are not supported with {alternative_packages=}: {exc}"
            )
        else:
            assert False, (
                f"Expected format {key} to work with non-seekable streams, but it failed with {exc!r}"
            )


@pytest.mark.sample_archives(archives=SINGLE_FILE_ARCHIVES, configs=["default", "altlibs"])
def test_open_compressed_stream_nonseekable(
    sample_archive: SampleArchive,
    sample_archive_path: str,
    archivey_config: ArchiveyConfig | None,
):
    alternative_packages = archivey_config is not None
    config = archivey_config

    with open(sample_archive_path, "rb") as f:
        data = f.read()

    stream = ensure_binaryio(NonSeekableBytesIO(data))

    try:
        with open_compressed_stream(stream, config=config) as f:
            assert not f.seekable()

            out = f.read()

    except (
        ArchiveStreamNotSeekableError
    ) as exc:  # pragma: no cover - environment dependent
        key = (sample_archive.creation_info.format, alternative_packages)
        logger.error(f"key: {key}")
        logger.error(f"EXPECTED_FAILURES: {EXPECTED_NON_SEEKABLE_FAILURES}")
        if key in EXPECTED_NON_SEEKABLE_FAILURES:
            pytest.xfail(
                f"Non-seekable {sample_archive.creation_info.format} are not supported with {alternative_packages=}: {exc}"
            )
        else:
            assert False, (
                f"Expected format {key} to work with non-seekable streams, but it failed with {exc!r}"
            )

    expected = sample_archive.contents.files[0].contents
    assert out == expected
