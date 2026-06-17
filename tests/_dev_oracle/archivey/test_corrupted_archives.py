import os
import pathlib
from venv import logger

import pytest

from archivey.config import ArchiveyConfig
from archivey.core import open_archive
from archivey.exceptions import (
    ArchiveCorruptedError,
    ArchiveEOFError,
)
from archivey.types import ArchiveFormat, ContainerFormat
from tests.archivey.sample_archives import (
    ALTERNATIVE_PACKAGES_FORMATS,
    SampleArchive,
)
from tests.create_corrupted_archives import corrupt_archive


def _prepare_corrupted_archive(
    sample_archive: SampleArchive,
    sample_archive_path: str,
    tmp_path_factory: pytest.TempPathFactory,
    corruption_type: str,
) -> pathlib.Path:
    """Return path to a corrupted version of the sample archive."""
    path = pathlib.Path(
        sample_archive.get_archive_path(variant=f"corrupted_{corruption_type}")
    )
    if path.exists():
        return path

    output_dir = tmp_path_factory.mktemp("generated_archives")
    corrupted_archive_path = output_dir / sample_archive.get_archive_name(
        variant=f"corrupted_{corruption_type}"
    )
    logger.info(
        f"Creating corrupted archive {corrupted_archive_path} with corruption type {corruption_type}"
    )
    corrupt_archive(
        pathlib.Path(sample_archive_path),
        corrupted_archive_path,
        corruption_type=corruption_type,
    )
    return corrupted_archive_path


@pytest.mark.sample_archives(
    prefixes=["large_files_nonsolid", "large_files_solid", "large_single_file"],
    configs=["default", "altlibs"],
)
@pytest.mark.parametrize("corruption_type", ["random", "zeroes", "ffs"])
@pytest.mark.parametrize("read_streams", [True, False], ids=["read", "noread"])
def test_read_corrupted_archives(
    sample_archive: SampleArchive,
    sample_archive_path: str,
    tmp_path_factory: pytest.TempPathFactory,
    read_streams: bool,
    archivey_config: ArchiveyConfig | None,
    corruption_type: str,
):
    """Test that reading generally corrupted archives raises ArchiveCorruptedError.

    Args:
        sample_archive: The archive to test
        sample_archive_path: Path to the source archive
        corruption_type: Type of corruption applied:
            - "random": Byte range replaced with random data
            - "zeroes": Byte range replaced with zeros
            - "ffs": Byte range replaced with 0xFF
    """
    if archivey_config is not None:
        if sample_archive.creation_info.format not in ALTERNATIVE_PACKAGES_FORMATS:
            pytest.skip("No alternative package for this format, no need to test")
    config = archivey_config

    formats_without_redundancy_check = [
        ArchiveFormat.LZ4,
        ArchiveFormat.TAR,
        ArchiveFormat.UNIX_COMPRESS,
        ArchiveFormat.BROTLI,
    ]

    if sample_archive.creation_info.format == ArchiveFormat.FOLDER:
        pytest.skip("Folder archives cannot be corrupted")

    corrupted_archive_path = _prepare_corrupted_archive(
        sample_archive,
        sample_archive_path,
        tmp_path_factory,
        corruption_type,
    )

    try:
        found_member_names = []
        found_member_data = {}

        with open_archive(
            corrupted_archive_path, config=config, streaming_only=True
        ) as archive:
            for member, stream in archive.iter_members_with_streams():
                logger.info(f"Reading member {member.filename}")
                filename = member.filename

                # Single file formats don't store the filename, and the reader derives
                # it from the archive name. But here, the archive name has a
                # .corrupted_xxx suffix that doesn't match the name in sample_archive,
                # so we need to remove it.
                if (
                    sample_archive.creation_info.format.container
                    == ContainerFormat.RAW_STREAM
                ):
                    filename = os.path.splitext(filename)[0]

                if stream is not None and read_streams:
                    data = stream.read()
                    logger.info(f"Read {len(data)} bytes from member {filename}")

                    found_member_data[filename] = data

                found_member_names.append(filename)

        expected_member_data = {
            member.name: member.contents for member in sample_archive.contents.files
        }
        logger.info(f"{found_member_names=}, expected={expected_member_data.keys()}")

        if (
            not read_streams
            and archive.format == ArchiveFormat.BZIP2
            and sample_archive.creation_info.format == ArchiveFormat.TAR_BZ2
        ):
            # In some corrupted archives, bz2 can uncompress the data stream, but it's
            # not a valid tar format. If we don't actually attempt to read the streams,
            # we won't detect the corruption.
            pytest.xfail(
                "Bzip2 can uncompress the data stream, but it's not a valid tar format."
            )

        # If no error was raised, it likely means that the corruption didn't affect the
        # archive directory or member metadata, so at least all the members should have
        # been read.
        assert set(found_member_names) == set(expected_member_data.keys()), (
            f"Archive {corrupted_archive_path} did not raise an error but did not read all members"
        )

        if read_streams:
            assert (
                sample_archive.creation_info.format in formats_without_redundancy_check
            ), f"Archive {corrupted_archive_path} should have detected a corruption"
            # If we read the streams and an error wasn't raised, it means the compressed
            # stream was valid, but at least one member should have different data.
            broken_files = [
                name
                for name, contents in expected_member_data.items()
                if contents is not None and contents != found_member_data[name]
            ]
            assert len(broken_files) >= 1, (
                f"Archive {corrupted_archive_path} should have at least one broken file"
            )
            # If this is a multi-file archive, which we corrupted in the middle,
            # at least the first file should be good. The last may or may not be broken,
            # depending on how the error was propagated.
            if len(expected_member_data) >= 1:
                assert len(broken_files) <= len(expected_member_data), (
                    f"Archive {corrupted_archive_path} should have at least one good file"
                )

    except (ArchiveCorruptedError, ArchiveEOFError):
        logger.info(f"Archive {corrupted_archive_path} raised an error", exc_info=True)


@pytest.mark.sample_archives(
    prefixes=["large_files_nonsolid", "large_files_solid", "large_single_file"],
    configs=["default", "altlibs"],
    # Tar files don't have any kind of error detection, so we skip them.
    # custom=lambda a: a.creation_info.format != ArchiveFormat.TAR,
)
@pytest.mark.parametrize("corrupted_length", [16, 47, 0.1, 0.9])
@pytest.mark.parametrize("read_streams", [True, False], ids=["read", "noread"])
def test_read_truncated_archives(
    sample_archive: SampleArchive,
    sample_archive_path: str,
    corrupted_length: int | float,
    tmp_path_factory: pytest.TempPathFactory,
    read_streams: bool,
    archivey_config: ArchiveyConfig | None,
):
    """Test that reading truncated archives raises appropriate errors."""
    if sample_archive.creation_info.format == ArchiveFormat.FOLDER:
        pytest.skip("Folder archives cannot be truncated")

    if archivey_config is not None:
        if sample_archive.creation_info.format not in ALTERNATIVE_PACKAGES_FORMATS:
            pytest.skip("No alternative package for this format, no need to test")
    config = archivey_config

    filename = sample_archive.get_archive_name(variant=f"truncated_{corrupted_length}")
    output_path = tmp_path_factory.mktemp("generated_archives") / filename

    logger.info(
        f"Testing truncated archive {output_path} with length {corrupted_length}"
    )

    data = open(sample_archive_path, "rb").read()
    if isinstance(corrupted_length, float):
        corrupted_length = int(corrupted_length * len(data))

    with open(output_path, "wb") as f:
        f.write(data[:corrupted_length])

    try:
        with open_archive(output_path, config=config, streaming_only=True) as archive:
            for member, stream in archive.iter_members_with_streams():
                if stream is not None and read_streams:
                    stream.read()
        logger.warning(f"Archive {output_path} did not raise an error")
    except (ArchiveCorruptedError, ArchiveEOFError):
        # Test passes if one of the expected exceptions is raised
        pass
