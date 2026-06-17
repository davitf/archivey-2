import logging
from typing import IO

import pytest
from archivey.config import ArchiveyConfig
from archivey.core import open_archive
from archivey.types import ContainerFormat, MemberType
from tests.archivey.sample_archives import (
    SYMLINK_ARCHIVES,
    SampleArchive,
)


def _first_regular_file(sample: SampleArchive):
    for f in sample.contents.files:
        if f.type == MemberType.FILE:
            return f
    raise ValueError("sample archive has no regular file")


logger = logging.getLogger(__name__)


@pytest.mark.sample_archives(prefixes=["large_files_nonsolid", "large_files_solid"])
def test_random_access_mode(sample_archive: SampleArchive, sample_archive_path: str):
    with open_archive(sample_archive_path) as archive:
        assert archive.has_random_access()
        members_if_available = archive.get_members_if_available()
        members = archive.get_members()

        assert members_if_available == members

        for sample_file in reversed(sample_archive.contents.files):
            f = archive.open(sample_file.name)
            data = f.read()
            assert sample_file.contents == data, f"{sample_file.name} contents mismatch"
            f.close()

        for sample_file in reversed(sample_archive.contents.files):
            with archive.open(sample_file.name) as f:
                data = f.read(100)
                assert len(data) == min(100, len(sample_file.contents or b""))
                data += f.read()
                assert sample_file.contents == data, (
                    f"{sample_file.name} contents mismatch"
                )

        sorted_members = sorted(members, key=lambda m: m.filename)
        files = [archive.open(m) for m in sorted_members]

        first_line = [f.readline() for f in files]
        rest_of_files = [f.read() for f in files[::-1]]
        rest_of_files.reverse()

        for i in range(len(files)):
            assert first_line[i] == f"Large file #{i + 1}\n".encode()
            assert (
                first_line[i] + rest_of_files[i]
                == sample_archive.contents.files[i].contents
            )

    for f in files:
        assert f.closed
        with pytest.raises(ValueError):
            f.read()


@pytest.mark.sample_archives(
    prefixes=["large_files_nonsolid", "large_files_solid"],
    configs=["default", "altlibs"],
)
@pytest.mark.parametrize("close_streams", [False, True], ids=["noclose", "close"])
def test_streaming_only_mode(
    sample_archive: SampleArchive,
    sample_archive_path: str,
    archivey_config: ArchiveyConfig | None,
    close_streams: bool,
):
    config = archivey_config or ArchiveyConfig()

    first_file = _first_regular_file(sample_archive)
    with open_archive(
        sample_archive_path, streaming_only=True, config=config
    ) as archive:
        assert not archive.has_random_access()

        with pytest.raises(ValueError):
            archive.get_members()
        with pytest.raises(ValueError):
            archive.open(first_file.name)

        info = archive.get_members_if_available()
        if sample_archive.creation_info.format.container == ContainerFormat.TAR:
            assert info is None
        else:
            assert info is not None and len(info) >= 1

        previous_stream: IO[bytes] | None = None
        for m, stream in archive.iter_members_with_streams():
            if previous_stream is not None:
                assert previous_stream.closed
                with pytest.raises(ValueError):
                    data = previous_stream.read()
                    logger.info(
                        f"previous_stream.read() = {data[:20]} -- {previous_stream=}"
                    )

            if m.is_link:
                assert m.link_target is not None
                assert stream is None
            elif m.is_dir:
                assert stream is None
            else:
                assert stream is not None
                seekable_before = stream.seekable()
                data = stream.read()
                seekable_after = stream.seekable()
                if seekable_before:
                    assert seekable_after
                if seekable_after:
                    print(m, f"Stream: {stream}")
                    stream.seek(0)
                    data_after = stream.read()
                    assert data == data_after

            previous_stream = stream

            assert (stream is None) == (m.type != MemberType.FILE)
            if close_streams and stream is not None:
                stream.close()


@pytest.mark.sample_archives(prefixes=["large_files_nonsolid", "large_files_solid"])
@pytest.mark.parametrize("streaming_only", [False, True], ids=["random", "stream"])
def test_iter_members_partial_reads(
    sample_archive: SampleArchive, sample_archive_path: str, streaming_only: bool
):
    """Reading some members fully, partially or not at all should not break iteration."""
    files = [f for f in sample_archive.contents.files if f.type == MemberType.FILE]
    assert len(files) == 5

    with open_archive(sample_archive_path, streaming_only=streaming_only) as archive:
        for i, (member, stream) in enumerate(
            archive.iter_members_with_streams(
                members=lambda m: m.type == MemberType.FILE
            )
        ):
            if member.filename not in {f.name for f in files}:
                continue

            info = next(f for f in files if f.name == member.filename)
            assert stream is not None

            if i % 3 == 0:
                assert stream.read() == (info.contents or b"")
            elif i % 3 == 1:
                partial_len = max(1, len(info.contents or b"") // 2)
                assert stream.read(partial_len) == (info.contents or b"")[:partial_len]
            else:
                pass


@pytest.mark.sample_archives(
    prefixes=["basic_nonsolid", "basic_solid", "duplicate_files"],
)
@pytest.mark.parametrize("streaming_only", [False, True], ids=["random", "stream"])
def test_iter_members_list_filter(
    sample_archive: SampleArchive, sample_archive_path: str, streaming_only: bool
):
    """Ensure iter_members_with_streams honours the filter callable."""
    if (
        sample_archive.filename.startswith("duplicate_files")
        and not sample_archive.creation_info.features.duplicate_files
    ):
        pytest.skip("Duplicate files feature is not enabled for this archive")

    file_names = {f.name for f in sample_archive.contents.files[::2]}
    file_contents = [
        (f.name, f.contents)
        for f in sample_archive.contents.files
        if f.name in file_names
    ]
    read_contents = []

    with open_archive(sample_archive_path, streaming_only=streaming_only) as archive:
        for member, stream in archive.iter_members_with_streams(members=file_names):
            assert member.filename in file_names
            read_contents.append(
                (member.filename, stream.read() if stream is not None else None)
            )

    assert sorted(file_contents) == sorted(read_contents), file_names


@pytest.mark.sample_archives(prefixes=["large_files_nonsolid", "large_files_solid"])
def test_streaming_only_allows_single_iteration(
    tmp_path, sample_archive: SampleArchive, sample_archive_path: str
):
    """Ensure streaming-only archives can be consumed only once."""
    with open_archive(sample_archive_path, streaming_only=True) as archive:
        next(archive.iter_members_with_streams())

        with pytest.raises(ValueError):
            next(archive.iter_members_with_streams())

        with pytest.raises(ValueError):
            archive.extractall(tmp_path)


@pytest.mark.sample_archives(prefixes=["large_files_nonsolid", "large_files_solid"])
def test_random_access_allows_multiple_iterations(
    tmp_path, sample_archive: SampleArchive, sample_archive_path: str
):
    """Random access readers should allow multiple iterations."""
    with open_archive(sample_archive_path) as archive:
        next(archive.iter_members_with_streams())
        list(archive.iter_members_with_streams())
        list(archive.iter_members_with_streams())


@pytest.mark.sample_archives(archives=SYMLINK_ARCHIVES)
def test_resolve_link_symlink_without_target(
    sample_archive: SampleArchive, sample_archive_path: str
) -> None:
    with open_archive(sample_archive_path) as archive:
        for sample_file in sample_archive.contents.files:
            member = archive.get_member(sample_file.name)
            resolved = archive.resolve_link(member)

            if member.type != MemberType.SYMLINK:
                assert member is resolved
                continue

            if sample_archive.creation_info.features.link_targets_in_header:
                assert member.link_target is not None, (
                    f"{sample_file.name=} {member.filename=} {member.link_target=}"
                )

            if member.link_target is None:
                assert resolved is None, (
                    f"{sample_file.name=} {member.filename=} {member.link_target=} {resolved=}"
                )
            else:
                assert resolved is not None
                logger.info(f"{member.filename=} {member.link_target=} {resolved=}")
                assert resolved.type in (MemberType.FILE, MemberType.DIR)
                if resolved.type == MemberType.FILE:
                    with archive.open(resolved) as f:
                        assert f.read() == sample_file.contents
                    with archive.open(member) as f:
                        assert f.read() == sample_file.contents
