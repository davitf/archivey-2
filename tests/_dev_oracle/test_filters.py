import pytest
from archivey.config import ExtractionFilter
from archivey.exceptions import ArchiveFilterError
from archivey.filters import (
    create_filter,
    fully_trusted,
    tar_filter,
)
from archivey.types import ArchiveMember, ContainerFormat, MemberType
from tests.archivey.sample_archives import SANITIZE_ARCHIVES, SampleArchive

from archivey import open_archive


@pytest.mark.sample_archives(archives=SANITIZE_ARCHIVES)
def test_fully_trusted_filter(sample_archive: SampleArchive, sample_archive_path: str):
    """Test the fully_trusted filter allows everything."""
    with open_archive(sample_archive_path) as archive:
        members = list(archive.iter_members_with_streams(filter=fully_trusted))

        # Should get all members without any filtering
        assert len(members) > 0

        # Check that problematic files are still present
        filenames = {m.filename for m, _ in members if m.type != MemberType.DIR}
        expected_filenames = {
            f.name for f in sample_archive.contents.files if f.type != MemberType.DIR
        }
        features = sample_archive.creation_info.features
        if features.replace_backslash_with_slash:
            expected_filenames = {
                name.replace("\\", "/") for name in expected_filenames
            }

        assert all(not m._edited_by_filter for m, _ in members)
        assert filenames == expected_filenames


@pytest.mark.sample_archives(archives=SANITIZE_ARCHIVES)
def test_tar_filter(sample_archive: SampleArchive, sample_archive_path: str):
    """Test the tar_filter raises errors on unsafe content."""
    with open_archive(sample_archive_path) as archive:
        with pytest.raises(
            ArchiveFilterError,
            match="(Absolute path not allowed|Path outside archive root|Symlink target outside archive root)",
        ):
            list(archive.iter_members_with_streams(filter=tar_filter))


@pytest.mark.sample_archives(archives=SANITIZE_ARCHIVES)
def test_data_filter(sample_archive: SampleArchive, sample_archive_path: str):
    """Test the data_filter raises errors on unsafe content."""
    with open_archive(sample_archive_path) as archive:
        with pytest.raises(
            ArchiveFilterError,
            match="(Absolute path not allowed|Path outside archive root|Symlink target outside archive root)",
        ):
            list(archive.iter_members_with_streams(filter=ExtractionFilter.DATA))


@pytest.mark.sample_archives(archives=SANITIZE_ARCHIVES)
def test_filter_with_raise_on_error_false(
    sample_archive: SampleArchive, sample_archive_path: str
):
    """Test filter that logs warnings instead of raising errors."""
    custom_filter = create_filter(
        for_data=False,
        sanitize_names=True,
        sanitize_link_targets=True,
        sanitize_permissions=True,
        raise_on_error=False,
    )

    archive_filenames = {
        f.name for f in sample_archive.contents.files if f.type != MemberType.DIR
    }
    expected_missing_filenames = {
        "/absfile.txt",
        "C:/windows_absfile.txt",
        "../outside.txt",
        "link_outside",
        "hardlink_outside",
    }
    # Only keep filenames that are present in the archive
    expected_missing_filenames &= archive_filenames
    expected_extra_filenames = set()
    if (
        "/absfile.txt" in expected_missing_filenames
        # Even if the file was written to the disk while generating the test folder,
        # it won't be present in the pseudo-archive, as it's outside the root folder.
        and sample_archive.creation_info.format.container != ContainerFormat.FOLDER
    ):
        expected_extra_filenames.add("absfile.txt")

    if sample_archive.creation_info.features.replace_backslash_with_slash:
        # The backslash in the filename is replaced with a slash, so the final filename
        # is rewritten as good.txt.
        expected_missing_filenames.add("backslash/..\\good.txt")

    with open_archive(sample_archive_path) as archive:
        # Should not raise an error, but should filter out problematic members
        members = [
            m
            for m, _ in archive.iter_members_with_streams(filter=custom_filter)
            if m.type != MemberType.DIR
        ]

        # Should get some members (the safe ones)
        assert len(members) > 0

        # Check that problematic files are filtered out
        filtered_filenames = {m.filename for m in members}

        assert archive_filenames - filtered_filenames == expected_missing_filenames
        assert filtered_filenames - archive_filenames == expected_extra_filenames

        if sample_archive.creation_info.features.replace_backslash_with_slash:
            # There should be two good.txt files.
            good_txt_files = [m for m in members if m.filename == "good.txt"]
            assert {archive.open(m).read() for m in good_txt_files} == {
                b"good",
                b"not the same as good.txt",
            }

        if "absfile.txt" in filtered_filenames:
            # Opening the member should work, even though the filename was sanitized.
            absfile = next(m for m in members if m.filename == "absfile.txt")
            assert absfile._edited_by_filter
            assert archive.open(absfile).read() == b"abs"

        if "hardlink_absfile" in filtered_filenames:
            # Even though the target of the hardlink has had its name sanitized,
            # opening it should still work.
            hardlink_absfile = next(
                m for m in members if m.filename == "hardlink_absfile"
            )
            assert hardlink_absfile._edited_by_filter
            assert archive.open(hardlink_absfile).read() == b"abs"


@pytest.mark.sample_archives(archives=SANITIZE_ARCHIVES)
def test_filter_without_name_sanitization(
    sample_archive: SampleArchive, sample_archive_path: str
):
    """Test filter that doesn't sanitize names."""
    custom_filter = create_filter(
        for_data=False,
        sanitize_names=False,
        sanitize_link_targets=True,
        sanitize_permissions=True,
        raise_on_error=True,
    )

    with open_archive(sample_archive_path) as archive:
        # Should still raise error due to link target sanitization
        with pytest.raises(
            ArchiveFilterError, match="Symlink target outside archive root"
        ):
            list(archive.iter_members_with_streams(filter=custom_filter))


@pytest.mark.sample_archives(archives=SANITIZE_ARCHIVES)
def test_filter_without_link_target_sanitization(
    sample_archive: SampleArchive, sample_archive_path: str
):
    """Test filter that doesn't sanitize link targets."""
    custom_filter = create_filter(
        for_data=False,
        sanitize_names=True,
        sanitize_link_targets=False,
        sanitize_permissions=True,
        raise_on_error=True,
    )

    with open_archive(sample_archive_path) as archive:
        name_issues = any(
            f.name.startswith("/") or f.name.startswith("../") or "/../" in f.name
            for f in sample_archive.contents.files
        )
        if name_issues:
            with pytest.raises(ArchiveFilterError):
                list(archive.iter_members_with_streams(filter=custom_filter))
        else:
            list(archive.iter_members_with_streams(filter=custom_filter))


@pytest.mark.sample_archives(archives=SANITIZE_ARCHIVES)
def test_filter_without_permission_sanitization(
    sample_archive: SampleArchive, sample_archive_path: str
):
    """Test filter that doesn't sanitize permissions."""
    custom_filter = create_filter(
        for_data=False,
        sanitize_names=True,
        sanitize_link_targets=True,
        sanitize_permissions=False,
        raise_on_error=True,
    )

    with open_archive(sample_archive_path) as archive:
        # Should still raise error due to name/link sanitization
        with pytest.raises(ArchiveFilterError):
            list(archive.iter_members_with_streams(filter=custom_filter))


@pytest.mark.sample_archives(archives=SANITIZE_ARCHIVES)
def test_data_filter_with_permission_changes(
    sample_archive: SampleArchive, sample_archive_path: str
):
    """Test data filter that changes permissions for files."""
    data_filter_custom = create_filter(
        for_data=True,
        sanitize_names=True,
        sanitize_link_targets=True,
        sanitize_permissions=True,
        raise_on_error=False,  # Don't raise to see permission changes
    )

    with open_archive(sample_archive_path) as archive:
        members = list(archive.iter_members_with_streams(filter=data_filter_custom))

        # Check that executable files have permissions changed
        for member, _ in members:
            assert member.uid is None
            assert member.gid is None
            assert member.uname is None
            assert member.gname is None

            if member.is_file and "exec.sh" in member.filename:
                # The filter removes executable bits but keeps owner permissions as 0o644
                # Original mode is 493 (0o755), should become 420 (0o644)
                expected_mode = 0o644  # 420
                actual_mode = member.mode if member.mode is not None else "None"
                assert member.mode == expected_mode, (
                    f"Expected {oct(expected_mode)}, got {oct(actual_mode) if actual_mode != 'None' else 'None'}"
                )


@pytest.mark.sample_archives(archives=SANITIZE_ARCHIVES)
def test_filter_combinations(sample_archive: SampleArchive, sample_archive_path: str):
    # Test minimal filtering
    minimal_filter = create_filter(
        for_data=False,
        sanitize_names=False,
        sanitize_link_targets=False,
        sanitize_permissions=False,
        raise_on_error=False,
    )

    with open_archive(sample_archive_path) as archive:
        members = list(archive.iter_members_with_streams(filter=minimal_filter))
        # Should get all members since no filtering is done
        assert len(members) > 0

        # Check that problematic files are still present
        filenames = [m.filename for m, _ in members]
        expected_names = [f.name for f in sample_archive.contents.files]
        features = sample_archive.creation_info.features
        if features.replace_backslash_with_slash:
            expected_names = [n.replace("\\", "/") for n in expected_names]

        if "/absfile.txt" in expected_names:
            assert any("/absfile.txt" in f for f in filenames)
        if "../outside.txt" in expected_names:
            assert any("../outside.txt" in f for f in filenames)


@pytest.mark.sample_archives(archives=SANITIZE_ARCHIVES)
def test_filter_error_messages(sample_archive: SampleArchive, sample_archive_path: str):
    """Test that filter errors have meaningful messages."""
    with open_archive(sample_archive_path) as archive:
        with pytest.raises(ArchiveFilterError) as exc_info:
            list(archive.iter_members_with_streams(filter=tar_filter))

        error_msg = str(exc_info.value)
        assert (
            "Absolute path not allowed" in error_msg
            or "Path outside archive root" in error_msg
            or "Symlink target outside archive root" in error_msg
        )


ERROR_CASES = [
    ("../outside.txt", "Path outside archive root"),
    ("link_outside", "Symlink target outside archive root"),
    ("hardlink_outside", "Hardlink target outside archive root"),
]


@pytest.mark.sample_archives(archives=SANITIZE_ARCHIVES)
@pytest.mark.parametrize(
    ("member_name", "pattern"),
    ERROR_CASES,
    ids=[c[0] for c in ERROR_CASES],
)
def test_tar_filter_individual_errors(
    sample_archive: SampleArchive,
    sample_archive_path: str,
    member_name: str,
    pattern: str,
):
    """Ensure tar_filter raises the correct error for each problematic member."""
    if member_name not in {f.name for f in sample_archive.contents.files}:
        pytest.skip(f"{member_name} not present in {sample_archive.filename}")

    with open_archive(sample_archive_path) as archive:
        with pytest.raises(ArchiveFilterError, match=pattern):
            list(
                archive.iter_members_with_streams(
                    members=[member_name], filter=tar_filter
                )
            )


@pytest.mark.sample_archives(archives=SANITIZE_ARCHIVES)
def test_filter_with_dest_path(sample_archive: SampleArchive, sample_archive_path: str):
    """Test filter behavior with destination path specified."""
    custom_filter = create_filter(
        for_data=False,
        sanitize_names=True,
        sanitize_link_targets=True,
        sanitize_permissions=True,
        raise_on_error=True,
    )

    with open_archive(sample_archive_path) as archive:
        with pytest.raises(ArchiveFilterError):
            list(archive.iter_members_with_streams(filter=custom_filter))


@pytest.mark.sample_archives(archives=SANITIZE_ARCHIVES[:1])
def test_broken_filter(sample_archive: SampleArchive, sample_archive_path: str):
    """Test that a broken filter raises an error."""
    first_member: ArchiveMember | None = None

    def broken_filter(member: ArchiveMember) -> ArchiveMember | None:
        # A filter that caches and always returns the first member. The code should
        # notice that the returned member is different from the input member.
        nonlocal first_member
        if first_member is None:
            first_member = member
        return first_member.replace()  # Create a copy

    with open_archive(sample_archive_path) as archive:
        with pytest.raises(
            ValueError, match="Filter returned a member with a different internal ID"
        ):
            list(archive.iter_members_with_streams(filter=broken_filter))
