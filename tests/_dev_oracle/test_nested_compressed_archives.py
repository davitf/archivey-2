import logging
import os
import shutil
import subprocess
from contextlib import nullcontext

import pytest
from archivey.archive_reader import ArchiveReader
from archivey.core import open_archive, open_compressed_stream
from archivey.exceptions import ArchiveStreamNotSeekableError, PackageNotInstalledError
from archivey.types import ArchiveFormat, ContainerFormat, StreamFormat
from tests.archivey.create_archives import (
    SINGLE_FILE_LIBRARY_OPENERS,
    create_7z_archive_with_py7zr,
    create_rar_archive_with_command_line,
    create_tar_archive_with_command_line,
    create_tar_archive_with_tarfile,
    create_zip_archive_with_zipfile,
)
from tests.archivey.sample_archives import (
    ALTERNATIVE_CONFIG,
    BASIC_ARCHIVES,
    SINGLE_FILE_ARCHIVES,
    ArchiveContents,
    File,
    SampleArchive,
    filter_archives,
)
from tests.archivey.test_open_nonseekable import EXPECTED_NON_SEEKABLE_FAILURES
from tests.archivey.testing_utils import skip_if_package_missing

_NESTED_ARCHIVES_DIR = os.path.join(
    os.path.dirname(__file__), "..", "test_archives", "nested"
)


def compress_stream(src: str, dst: str, fmt: StreamFormat) -> str:
    if fmt == StreamFormat.UNIX_COMPRESS:
        # Use a pre-built .Z file if one exists for this inner archive.
        prebuilt = os.path.join(_NESTED_ARCHIVES_DIR, os.path.basename(src) + ".Z")
        if os.path.exists(prebuilt):
            shutil.copy(prebuilt, dst)
            return dst
        if shutil.which("compress") is None:
            pytest.skip("compress command not available on this platform")
        with open(dst, "wb") as f_out:
            process = subprocess.Popen(
                ["compress", "-c", src],
                stdout=f_out,
            )
            process.wait()
            if process.returncode != 0:
                raise subprocess.CalledProcessError(
                    process.returncode, ["compress", "-c", src]
                )
        return dst

    opener = SINGLE_FILE_LIBRARY_OPENERS[fmt]
    if opener is None:
        pytest.skip(f"Required library for {fmt} is not installed")
    with open(src, "rb") as f_in, opener(dst, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    return dst


def create_archive_with_member(
    outer_format: ArchiveFormat, inner_path: str, dst: str
) -> str:
    data = open(inner_path, "rb").read()
    contents = ArchiveContents(
        file_basename="outer", files=[File(os.path.basename(inner_path), 1, data)]
    )

    if outer_format.container == ContainerFormat.ZIP:
        create_zip_archive_with_zipfile(dst, contents, ArchiveFormat.ZIP)
    elif outer_format.container == ContainerFormat.RAR:
        create_rar_archive_with_command_line(dst, contents, ArchiveFormat.RAR)
    elif outer_format.container == ContainerFormat.SEVENZIP:
        create_7z_archive_with_py7zr(dst, contents, ArchiveFormat.SEVENZIP)
    elif outer_format.container == ContainerFormat.TAR:
        if outer_format.stream == StreamFormat.UNIX_COMPRESS:
            if shutil.which("compress") is None:
                pytest.skip("compress command not available on this platform")
            create_tar_archive_with_command_line(dst, contents, outer_format)
        else:
            create_tar_archive_with_tarfile(dst, contents, outer_format)
    else:
        raise AssertionError(f"Unsupported outer format {outer_format}")
    return dst


logger = logging.getLogger(__name__)


def check_archive_iter_members(archive: ArchiveReader):
    for _, member_stream in archive.iter_members_with_streams():
        has_member = True
        if member_stream is not None:
            member_stream.read()
        assert has_member


@pytest.mark.parametrize(
    "outer_stream_format",
    set(StreamFormat) - {StreamFormat.UNCOMPRESSED},
)
@pytest.mark.parametrize(
    "inner_archive",
    filter_archives(
        BASIC_ARCHIVES + SINGLE_FILE_ARCHIVES,
        custom_filter=lambda a: a.creation_info.format != ArchiveFormat.FOLDER,
    ),
    ids=lambda a: a.filename,
)
@pytest.mark.parametrize(
    "alternative_packages", [False, True], ids=["default", "altlibs"]
)
@pytest.mark.parametrize(
    "open_inner_streaming_only",
    [False, True],
)
def test_open_archive_from_compressed_stream(
    outer_stream_format: StreamFormat,
    inner_archive: SampleArchive,
    tmp_path,
    alternative_packages: bool,
    open_inner_streaming_only: bool,
):
    config = ALTERNATIVE_CONFIG if alternative_packages else None
    outer_format = ArchiveFormat(ContainerFormat.RAW_STREAM, outer_stream_format)

    skip_if_package_missing(outer_format, config)
    skip_if_package_missing(inner_archive.creation_info.format, config)

    if (
        alternative_packages
        and outer_stream_format == StreamFormat.BZIP2
        and inner_archive.filename.endswith(".bz2")
    ):
        pytest.xfail("prevent segfault")

    logger.info(
        f"alternative_packages: {alternative_packages}, outer_format: {outer_stream_format}, inner_archive.filename: {inner_archive.filename}"
    )

    inner_path = inner_archive.get_archive_path()
    compressed_path = os.path.join(
        tmp_path,
        os.path.basename(inner_path) + "." + outer_format.file_extension(),
    )
    compress_stream(inner_path, compressed_path, outer_stream_format)

    with open_compressed_stream(compressed_path, config=config) as stream:
        with open_archive(
            stream, config=config, streaming_only=open_inner_streaming_only
        ) as archive:
            assert archive.format == inner_archive.creation_info.format
            check_archive_iter_members(archive)


ALL_TAR_FORMATS = [
    ArchiveFormat(ContainerFormat.TAR, stream_format) for stream_format in StreamFormat
]


def expect_raise_if(condition: bool, exc_type: type[Exception]):
    if condition:
        return pytest.raises(exc_type)

    return nullcontext()


@pytest.mark.parametrize(
    "outer_format",
    [
        ArchiveFormat.ZIP,
        ArchiveFormat.RAR,
        ArchiveFormat.SEVENZIP,
    ]
    + ALL_TAR_FORMATS,
    ids=lambda a: a.file_extension(),
)
@pytest.mark.parametrize(
    "inner_archive",
    filter_archives(
        BASIC_ARCHIVES + SINGLE_FILE_ARCHIVES,
        custom_filter=lambda a: a.creation_info.format != ArchiveFormat.FOLDER,
    ),
    ids=lambda a: a.filename,
)
@pytest.mark.parametrize(
    "alternative_packages", [False, True], ids=["default", "altlibs"]
)
@pytest.mark.parametrize(
    "open_outer_streaming_only",
    [False, True],
    ids=["outer_random", "outer_streaming"],
)
def test_open_archive_from_member(
    outer_format: ArchiveFormat,
    inner_archive: SampleArchive,
    tmp_path,
    alternative_packages: bool,
    open_outer_streaming_only: bool,
):
    config = ALTERNATIVE_CONFIG if alternative_packages else None

    skip_if_package_missing(outer_format, config)
    skip_if_package_missing(inner_archive.creation_info.format, config)

    inner_path = inner_archive.get_archive_path()
    outer_path = os.path.join(tmp_path, "outer." + outer_format.file_extension())
    try:
        create_archive_with_member(outer_format, inner_path, outer_path)
    except PackageNotInstalledError as exc:
        pytest.skip(str(exc))

    expect_non_seekable_failure = (
        inner_archive.creation_info.format,
        alternative_packages,
    ) in EXPECTED_NON_SEEKABLE_FAILURES

    # Try opening the inner archive in random mode. It should work if the outer
    # archive is seekable and it provides seekable streams (only 7z doesn't).
    with open_archive(
        outer_path, config=config, streaming_only=open_outer_streaming_only
    ) as outer:
        assert outer.get_archive_info().format == outer_format

        outer_has_member = False
        for member, stream in outer.iter_members_with_streams():
            assert member.filename.endswith(os.path.basename(inner_path))
            assert stream is not None
            outer_has_member = True

            if open_outer_streaming_only:
                assert not stream.seekable()

            with expect_raise_if(
                not stream.seekable(),
                ArchiveStreamNotSeekableError,
            ):
                with open_archive(
                    stream, config=config, streaming_only=False
                ) as archive:
                    assert archive.format == inner_archive.creation_info.format
                    assert archive.get_members() is not None
                    check_archive_iter_members(archive)

        assert outer_has_member

    # Try opening the inner archive in streaming mode. It should work if the stream is
    # seekable or if the inner library support opening non-seekable streams in
    # streaming mode.
    with open_archive(
        outer_path, config=config, streaming_only=open_outer_streaming_only
    ) as outer:
        assert outer.get_archive_info().format == outer_format

        outer_has_member = False
        for member, stream in outer.iter_members_with_streams():
            assert member.filename.endswith(os.path.basename(inner_path))
            assert stream is not None
            outer_has_member = True

            if open_outer_streaming_only:
                assert not stream.seekable()

            with expect_raise_if(
                not stream.seekable() and expect_non_seekable_failure,
                ArchiveStreamNotSeekableError,
            ):
                with open_archive(
                    stream, config=config, streaming_only=True
                ) as archive:
                    assert archive.format == inner_archive.creation_info.format
                    check_archive_iter_members(archive)

        assert outer_has_member
