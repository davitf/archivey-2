import io
import logging

import pytest
from tests.archivey.sample_archives import (
    ALTERNATIVE_CONFIG,
    BASIC_ARCHIVES,
    LARGE_ARCHIVES,
    SampleArchive,
    filter_archives,
)
from tests.archivey.test_open_compressed_stream import SINGLE_FILE_ARCHIVES
from tests.archivey.testing_utils import skip_if_package_missing

from archivey.config import ArchiveyConfig
from archivey.core import open_archive, open_compressed_stream
from archivey.internal.io_helpers import IOStats, StatsIO, ensure_binaryio
from archivey.types import ArchiveFormat

logger = logging.getLogger(__name__)


@pytest.mark.parametrize(
    "sample_archive",
    filter_archives(
        BASIC_ARCHIVES + LARGE_ARCHIVES,
        custom_filter=lambda a: a.creation_info.format not in (ArchiveFormat.FOLDER,),
    ),
    ids=lambda a: a.filename,
)
@pytest.mark.parametrize(
    "alternative_packages", [False, True], ids=["defaultlibs", "altlibs"]
)
def test_open_archive_statsio(
    sample_archive: SampleArchive, sample_archive_path: str, alternative_packages: bool
):
    """Ensure open_archive can read from StatsIO-wrapped streams and tracks statistics correctly."""
    if alternative_packages:
        config = ArchiveyConfig(
            use_rapidgzip=True,
            use_indexed_bzip2=True,
            use_zstandard=True,
        )
    else:
        config = None

    skip_if_package_missing(sample_archive.creation_info.format, config)

    with open(sample_archive_path, "rb") as f:
        data = f.read()

    # Create StatsIO wrapper around the data
    stats = IOStats()
    stream = StatsIO(io.BytesIO(data), stats)

    # Track initial stats
    initial_bytes_read = stats.bytes_read
    initial_seek_calls = stats.seek_calls
    initial_read_ranges = len(stats.read_ranges)

    with open_archive(stream, config=config) as archive:
        has_member = False
        total_member_bytes = 0

        for member, member_stream in archive.iter_members_with_streams():
            has_member = True
            if member_stream is not None:
                member_data = member_stream.read()
                total_member_bytes += len(member_data)

        assert has_member

    # Verify that statistics were tracked
    assert stats.bytes_read > initial_bytes_read, (
        "No bytes were read according to stats"
    )
    assert stats.seek_calls >= initial_seek_calls, "No seek operations were tracked"
    assert len(stats.read_ranges) > initial_read_ranges, "No read ranges were tracked"


@pytest.mark.parametrize(
    "sample_archive",
    filter_archives(
        BASIC_ARCHIVES + LARGE_ARCHIVES,
        custom_filter=lambda a: a.creation_info.format not in (ArchiveFormat.FOLDER,),
    ),
    ids=lambda a: a.filename,
)
@pytest.mark.parametrize(
    "alternative_packages", [False, True], ids=["defaultlibs", "altlibs"]
)
def test_open_archive_statsio_streaming_mode(
    sample_archive: SampleArchive, sample_archive_path: str, alternative_packages: bool
):
    """Ensure open_archive can read from StatsIO-wrapped streams in streaming mode."""
    config = ALTERNATIVE_CONFIG if alternative_packages else None

    skip_if_package_missing(sample_archive.creation_info.format, config)

    with open(sample_archive_path, "rb") as f:
        data = f.read()

    # Create StatsIO wrapper around the data
    stats = IOStats()
    stream = StatsIO(io.BytesIO(data), stats)

    # Track initial stats
    initial_bytes_read = stats.bytes_read
    initial_seek_calls = stats.seek_calls
    initial_read_ranges = len(stats.read_ranges)

    with open_archive(stream, streaming_only=True, config=config) as archive:
        has_member = False
        total_member_bytes = 0

        for member, member_stream in archive.iter_members_with_streams():
            has_member = True
            if member_stream is not None:
                member_data = member_stream.read()
                total_member_bytes += len(member_data)

        assert has_member

    # Verify that statistics were tracked
    assert stats.bytes_read > initial_bytes_read
    assert stats.seek_calls >= initial_seek_calls
    assert len(stats.read_ranges) > initial_read_ranges

    # Verify that a significant portion of the archive was read. Some metadata
    # may be skipped by the underlying library, so we only require 70% of the
    # archive bytes to be consumed.
    assert stats.bytes_read > len(data) * 0.7, (
        f"Expected more than 70% of {len(data)} bytes read, got {stats.bytes_read}"
    )


@pytest.mark.parametrize(
    "sample_archive", SINGLE_FILE_ARCHIVES, ids=lambda a: a.filename
)
@pytest.mark.parametrize(
    "alternative_packages", [False, True], ids=["defaultlibs", "altlibs"]
)
def test_open_compressed_stream_statsio(
    sample_archive: SampleArchive, sample_archive_path: str, alternative_packages: bool
):
    """Ensure open_compressed_stream can read from StatsIO-wrapped streams and tracks statistics correctly."""
    config = ALTERNATIVE_CONFIG if alternative_packages else None

    skip_if_package_missing(sample_archive.creation_info.format, config)

    with open(sample_archive_path, "rb") as f:
        data = f.read()

    # Create StatsIO wrapper around the data
    stats = IOStats()
    stream = ensure_binaryio(StatsIO(io.BytesIO(data), stats))

    # Track initial stats
    initial_bytes_read = stats.bytes_read
    initial_seek_calls = stats.seek_calls
    initial_read_ranges = len(stats.read_ranges)

    with open_compressed_stream(stream, config=config) as f:
        out = f.read()

    # Verify that statistics were tracked
    assert stats.bytes_read > initial_bytes_read, (
        "No bytes were read according to stats"
    )
    assert stats.seek_calls >= initial_seek_calls, "No seek operations were tracked"
    assert len(stats.read_ranges) > initial_read_ranges, "No read ranges were tracked"

    # Verify that a significant portion of the archive was read. Some metadata
    # may be skipped by the underlying library, so we only require 70% of the
    # archive bytes to be consumed.
    assert stats.bytes_read > len(data) * 0.7, (
        f"Expected more than 70% of {len(data)} bytes read, got {stats.bytes_read}"
    )

    # Verify the decompressed content is correct
    expected = sample_archive.contents.files[0].contents
    assert out == expected


@pytest.mark.parametrize(
    "sample_archive",
    filter_archives(
        BASIC_ARCHIVES,
        custom_filter=lambda a: a.creation_info.format not in (ArchiveFormat.FOLDER,),
    ),
    ids=lambda a: a.filename,
)
@pytest.mark.parametrize(
    "alternative_packages", [False, True], ids=["defaultlibs", "altlibs"]
)
def test_open_archive_statsio_seek_operations(
    sample_archive: SampleArchive, sample_archive_path: str, alternative_packages: bool
):
    """Test that seek operations are properly tracked by StatsIO when opening archives."""
    config = ALTERNATIVE_CONFIG if alternative_packages else None

    skip_if_package_missing(sample_archive.creation_info.format, config)

    with open(sample_archive_path, "rb") as f:
        data = f.read()

    # Create StatsIO wrapper around the data
    stats = IOStats()
    stream = StatsIO(io.BytesIO(data), stats)

    # Track initial stats
    initial_seek_calls = stats.seek_calls
    initial_read_ranges = len(stats.read_ranges)

    with open_archive(stream, config=config) as archive:
        # Get member list first (this may trigger seeks)
        members = archive.get_members()
        assert len(members) > 0

        # Read a few members to trigger more I/O operations
        for i, member in enumerate(members[:3]):  # Read first 3 members
            if member.type.value == "file":  # Only try to open file members
                with archive.open(member) as member_stream:
                    member_stream.read()

    # Verify that seek operations were tracked
    assert stats.seek_calls >= initial_seek_calls
    assert len(stats.read_ranges) > initial_read_ranges

    # Verify read ranges are properly formatted
    for read_range in stats.read_ranges:
        assert len(read_range) == 2
        assert read_range[0] >= 0
        assert read_range[1] >= 0


@pytest.mark.parametrize(
    "sample_archive",
    filter_archives(
        BASIC_ARCHIVES,
        custom_filter=lambda a: a.creation_info.format not in (ArchiveFormat.FOLDER,),
    ),
    ids=lambda a: a.filename,
)
@pytest.mark.parametrize(
    "alternative_packages", [False, True], ids=["defaultlibs", "altlibs"]
)
def test_open_archive_statsio_readinto_operations(
    sample_archive: SampleArchive, sample_archive_path: str, alternative_packages: bool
):
    """Test that readinto operations are properly tracked by StatsIO when opening archives."""
    config = ALTERNATIVE_CONFIG if alternative_packages else None

    skip_if_package_missing(sample_archive.creation_info.format, config)

    with open(sample_archive_path, "rb") as f:
        data = f.read()

    # Create StatsIO wrapper around the data
    stats = IOStats()
    stream = StatsIO(io.BytesIO(data), stats)

    # Track initial stats
    initial_bytes_read = stats.bytes_read
    initial_read_ranges = len(stats.read_ranges)

    with open_archive(stream, config=config) as archive:
        # Read members using readinto to test that operation
        for member in archive.get_members():
            if member.type.value == "file":  # Only try to open file members
                with archive.open(member) as member_stream:
                    buffer = bytearray(1024)
                    while True:
                        logger.debug(f"readinto {member_stream}")
                        bytes_read = member_stream.readinto(buffer)  # type: ignore
                        if bytes_read == 0:
                            break

    # Verify that bytes were read
    assert stats.bytes_read > initial_bytes_read, (
        "No bytes were read according to stats"
    )
    assert len(stats.read_ranges) > initial_read_ranges, "No read ranges were tracked"


@pytest.mark.parametrize(
    "sample_archive",
    filter_archives(
        BASIC_ARCHIVES,
        custom_filter=lambda a: a.creation_info.format not in (ArchiveFormat.FOLDER,),
    ),
    ids=lambda a: a.filename,
)
@pytest.mark.parametrize(
    "alternative_packages", [False, True], ids=["defaultlibs", "altlibs"]
)
def test_open_archive_statsio_multiple_opens(
    sample_archive: SampleArchive, sample_archive_path: str, alternative_packages: bool
):
    """Test that StatsIO properly tracks statistics across multiple archive opens."""
    config = ALTERNATIVE_CONFIG if alternative_packages else None

    skip_if_package_missing(sample_archive.creation_info.format, config)

    with open(sample_archive_path, "rb") as f:
        data = f.read()

    # Create StatsIO wrapper around the data
    stats = IOStats()
    stream = StatsIO(io.BytesIO(data), stats)

    # First open
    with open_archive(stream, config=config) as archive:
        members = archive.get_members()
        assert len(members) > 0

    first_open_bytes = stats.bytes_read
    first_open_seeks = stats.seek_calls
    first_open_ranges = len(stats.read_ranges)

    # Second open (should accumulate stats)
    with open_archive(stream, config=config) as archive:
        members = archive.get_members()
        assert len(members) > 0

    # Verify that stats accumulated across opens
    assert stats.bytes_read > first_open_bytes, "Stats should accumulate across opens"
    assert stats.seek_calls >= first_open_seeks, (
        "Seek stats should accumulate across opens"
    )
    assert len(stats.read_ranges) > first_open_ranges, (
        "Read ranges should accumulate across opens"
    )


@pytest.mark.parametrize(
    "sample_archive",
    filter_archives(
        BASIC_ARCHIVES,
        custom_filter=lambda a: a.creation_info.format not in (ArchiveFormat.FOLDER,),
    ),
    ids=lambda a: a.filename,
)
@pytest.mark.parametrize(
    "alternative_packages", [False, True], ids=["defaultlibs", "altlibs"]
)
def test_open_archive_statsio_io_methods(
    sample_archive: SampleArchive, sample_archive_path: str, alternative_packages: bool
):
    """Test that StatsIO properly delegates IO methods to the underlying stream."""
    if alternative_packages:
        config = ArchiveyConfig(
            use_rapidgzip=True,
            use_indexed_bzip2=True,
            use_zstandard=True,
        )
    else:
        config = None

    skip_if_package_missing(sample_archive.creation_info.format, config)

    with open(sample_archive_path, "rb") as f:
        data = f.read()

    # Create StatsIO wrapper around the data
    stats = IOStats()
    underlying_stream = io.BytesIO(data)
    stream = StatsIO(underlying_stream, stats)

    # Test that IO methods are properly delegated
    assert stream.readable() == underlying_stream.readable()
    assert stream.writable() == underlying_stream.writable()
    assert stream.seekable() == underlying_stream.seekable()

    # Test that we can still open the archive normally
    with open_archive(stream, config=config) as archive:
        members = archive.get_members()
        assert len(members) > 0

    # Verify that statistics were tracked
    assert stats.bytes_read > 0, "No bytes were read according to stats"
    assert len(stats.read_ranges) > 1, "No read ranges were tracked"
