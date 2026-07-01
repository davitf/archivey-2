"""Tests for the directory pseudo-backend."""

from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

import archivey
from archivey import (
    ArchiveFormat,
    ArchiveMember,
    MemberType,
    open_archive,
)
from archivey.cost import AccessCost, ListingCost, StreamCapability

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def simple_dir(tmp_path: Path) -> Path:
    """A minimal directory: two files, one subdir, one nested file."""
    (tmp_path / "a.txt").write_bytes(b"hello")
    (tmp_path / "b.txt").write_bytes(b"world")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.txt").write_bytes(b"nested")
    return tmp_path


@pytest.fixture
def symlink_dir(tmp_path: Path) -> Path:
    """Directory containing a symlink pointing to a real file."""
    (tmp_path / "real.txt").write_bytes(b"real content")
    os.symlink("real.txt", tmp_path / "link.txt")
    return tmp_path


@pytest.fixture
def deep_dir(tmp_path: Path) -> Path:
    """Directory with several levels of nesting."""
    (tmp_path / "root.txt").write_bytes(b"root")
    level1 = tmp_path / "level1"
    level1.mkdir()
    (level1 / "l1.txt").write_bytes(b"level1")
    level2 = level1 / "level2"
    level2.mkdir()
    (level2 / "l2.txt").write_bytes(b"level2")
    return tmp_path


# ---------------------------------------------------------------------------
# Format detection and info
# ---------------------------------------------------------------------------


def test_open_directory_returns_reader(simple_dir: Path) -> None:
    with open_archive(simple_dir) as reader:
        assert reader.format == ArchiveFormat.DIRECTORY  # type: ignore[attr-defined]


def test_open_directory_as_string(simple_dir: Path) -> None:
    with open_archive(str(simple_dir)) as reader:
        assert reader.format == ArchiveFormat.DIRECTORY  # type: ignore[attr-defined]


def test_archive_info_format(simple_dir: Path) -> None:
    with open_archive(simple_dir) as reader:
        info = reader.info
        assert info.format == ArchiveFormat.DIRECTORY  # type: ignore[attr-defined]
        assert info.is_solid is False
        assert info.is_encrypted is False
        assert info.is_multivolume is False
        assert info.format_version is None
        assert info.comment is None


def test_cost_receipt(simple_dir: Path) -> None:
    with open_archive(simple_dir) as reader:
        cost = reader.cost
        assert cost.listing_cost == ListingCost.INDEXED
        assert cost.access_cost == AccessCost.DIRECT
        assert cost.stream_capability == StreamCapability.SEEKABLE
        assert cost.solid_block_count is None


# ---------------------------------------------------------------------------
# Member listing
# ---------------------------------------------------------------------------


def test_members_returns_list(simple_dir: Path) -> None:
    with open_archive(simple_dir) as reader:
        members = reader.members()
        assert isinstance(members, list)
        assert len(members) > 0


def test_members_include_files_and_dirs(simple_dir: Path) -> None:
    with open_archive(simple_dir) as reader:
        members = reader.members()
        names = {m.name for m in members}
        assert "a.txt" in names
        assert "b.txt" in names
        assert "sub/" in names
        assert "sub/c.txt" in names


def test_members_have_correct_types(simple_dir: Path) -> None:
    with open_archive(simple_dir) as reader:
        by_name = {m.name: m for m in reader.members()}
        assert by_name["a.txt"].type == MemberType.FILE
        assert by_name["b.txt"].type == MemberType.FILE
        assert by_name["sub/"].type == MemberType.DIRECTORY
        assert by_name["sub/c.txt"].type == MemberType.FILE


def test_members_file_sizes(simple_dir: Path) -> None:
    with open_archive(simple_dir) as reader:
        by_name = {m.name: m for m in reader.members()}
        assert by_name["a.txt"].size == 5
        assert by_name["b.txt"].size == 5
        assert by_name["sub/c.txt"].size == 6
        # Directories have no size
        assert by_name["sub/"].size is None


def test_members_have_modified_timestamp(simple_dir: Path) -> None:
    with open_archive(simple_dir) as reader:
        for member in reader.members():
            assert member.modified is not None


def test_members_have_mode(simple_dir: Path) -> None:
    with open_archive(simple_dir) as reader:
        for member in reader.members():
            assert member.mode is not None


def test_members_have_member_id(simple_dir: Path) -> None:
    with open_archive(simple_dir) as reader:
        for member in reader.members():
            assert member.member_id >= 0


def test_member_ids_are_unique(simple_dir: Path) -> None:
    with open_archive(simple_dir) as reader:
        ids = [m.member_id for m in reader.members()]
        assert len(ids) == len(set(ids))


def test_member_archive_id_set(simple_dir: Path) -> None:
    with open_archive(simple_dir) as reader:
        for member in reader.members():
            assert member.archive_id  # non-empty string


# ---------------------------------------------------------------------------
# Iteration order: a directory's own non-dir entries before its subdirectories
# ---------------------------------------------------------------------------


def test_non_dirs_listed_before_subdirs(simple_dir: Path) -> None:
    with open_archive(simple_dir) as reader:
        names = [m.name for m in reader]
    # Top-level files precede the subdirectory entry (and therefore its contents).
    assert names.index("a.txt") < names.index("sub/")
    assert names.index("b.txt") < names.index("sub/")
    # Parent-before-children still holds within the subtree.
    assert names.index("sub/") < names.index("sub/c.txt")


# ---------------------------------------------------------------------------
# Iteration
# ---------------------------------------------------------------------------


def test_iter_yields_members(simple_dir: Path) -> None:
    with open_archive(simple_dir) as reader:
        members = list(reader)
        assert len(members) > 0
        for m in members:
            assert isinstance(m, ArchiveMember)


def test_no_len(simple_dir: Path) -> None:
    # The reader is deliberately not a collection: no __len__ (see archive-reading);
    # counting goes through members() or iteration.
    with open_archive(simple_dir) as reader:
        with pytest.raises(TypeError):
            len(reader)
        assert len(reader.members()) == len(list(reader))


def test_contains_is_identity_membership(simple_dir: Path, tmp_path: Path) -> None:
    # `member in reader` is identity-based: True for a member this reader yielded,
    # False for one from a different reader; a string operand raises TypeError
    # (name lookup is get()).
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    (other_dir / "b.txt").write_bytes(b"x")
    with open_archive(simple_dir) as reader, open_archive(other_dir) as other:
        member = reader.get("a.txt")
        assert member is not None
        assert member in reader
        assert member not in other
        with pytest.raises(TypeError):
            "a.txt" in reader  # noqa: B015 - the expression itself must raise


def test_get_returns_member(simple_dir: Path) -> None:
    with open_archive(simple_dir) as reader:
        member = reader.get("a.txt")
        assert member is not None
        assert member.name == "a.txt"
        assert member.type == MemberType.FILE


def test_open_missing_name_raises_key_error(simple_dir: Path) -> None:
    with open_archive(simple_dir) as reader:
        with pytest.raises(KeyError):
            reader.open("does_not_exist.txt")


def test_get_returns_none_for_missing(simple_dir: Path) -> None:
    with open_archive(simple_dir) as reader:
        assert reader.get("does_not_exist.txt") is None


# ---------------------------------------------------------------------------
# Reading file content
# ---------------------------------------------------------------------------


def test_read_file_by_name(simple_dir: Path) -> None:
    with open_archive(simple_dir) as reader:
        data = reader.read("a.txt")
        assert data == b"hello"


def test_read_file_by_member(simple_dir: Path) -> None:
    with open_archive(simple_dir) as reader:
        member = reader.get("b.txt")
        data = reader.read(member)
        assert data == b"world"


def test_open_file_returns_binary_io(simple_dir: Path) -> None:
    with open_archive(simple_dir) as reader:
        with reader.open("a.txt") as f:
            data = f.read()
        assert data == b"hello"


def test_read_nested_file(simple_dir: Path) -> None:
    with open_archive(simple_dir) as reader:
        data = reader.read("sub/c.txt")
        assert data == b"nested"


# ---------------------------------------------------------------------------
# Symlink handling
# ---------------------------------------------------------------------------


def test_symlink_member_type(symlink_dir: Path) -> None:
    with open_archive(symlink_dir) as reader:
        by_name = {m.name: m for m in reader.members()}
        assert "link.txt" in by_name
        assert by_name["link.txt"].type == MemberType.SYMLINK
        assert by_name["link.txt"].link_target == "real.txt"


def test_symlink_link_target_member_resolved(symlink_dir: Path) -> None:
    with open_archive(symlink_dir) as reader:
        link = reader.get("link.txt")
        assert link.link_target_member is not None
        assert link.link_target_member.name == "real.txt"


def test_open_symlink_follows_to_real_content(symlink_dir: Path) -> None:
    with open_archive(symlink_dir) as reader:
        data = reader.read("link.txt")
        assert data == b"real content"


# ---------------------------------------------------------------------------
# Windows NTFS junction handling (runs only on the Windows CI leg)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are Windows-only")
@pytest.mark.skipif(
    sys.version_info < (3, 12), reason="os.DirEntry.is_junction() needs Python 3.12+"
)
def test_windows_junction_detected_and_not_traversed(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "inside.txt").write_bytes(b"inside")
    junction = tmp_path / "jx"
    # mklink /J creates a junction and needs no admin rights (unlike a symlink).
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
        check=True,
        capture_output=True,
    )

    with open_archive(tmp_path) as reader:
        by_name = {m.name: m for m in reader.members()}

    junction_member = by_name["jx"]
    # A junction is surfaced as a symlink-like leaf, flagged via is_junction.
    assert junction_member.type == MemberType.SYMLINK
    assert junction_member.is_junction is True
    # It is NOT walked through: its contents do not appear under the junction name.
    assert "jx/inside.txt" not in by_name
    # The real target directory, walked directly, still yields its contents.
    assert "target/inside.txt" in by_name


# ---------------------------------------------------------------------------
# stream_members
# ---------------------------------------------------------------------------


def test_stream_members_yields_all(simple_dir: Path) -> None:
    with open_archive(simple_dir) as reader:
        pairs = list(reader.stream_members())
        assert len(pairs) == len(reader.members())


def test_stream_members_files_have_stream(simple_dir: Path) -> None:
    with open_archive(simple_dir) as reader:
        for member, stream in reader.stream_members():
            if member.is_file:
                assert stream is not None
                stream.close()
            else:
                assert stream is None


def test_stream_members_with_filter(simple_dir: Path) -> None:
    with open_archive(simple_dir) as reader:
        pairs = list(reader.stream_members(members=lambda m: m.is_file))
        for member, stream in pairs:
            assert member.is_file
            if stream is not None:
                stream.close()


# ---------------------------------------------------------------------------
# Convenience properties
# ---------------------------------------------------------------------------


def test_member_is_file_property(simple_dir: Path) -> None:
    with open_archive(simple_dir) as reader:
        m = reader.get("a.txt")
        assert m.is_file is True
        assert m.is_dir is False
        assert m.is_link is False


def test_member_is_dir_property(simple_dir: Path) -> None:
    with open_archive(simple_dir) as reader:
        m = reader.get("sub/")
        assert m.is_dir is True
        assert m.is_file is False


# ---------------------------------------------------------------------------
# Context manager and close
# ---------------------------------------------------------------------------


def test_context_manager_closes(simple_dir: Path) -> None:
    with open_archive(simple_dir) as reader:
        assert not reader._closed
    assert reader._closed


def test_close_is_idempotent(simple_dir: Path) -> None:
    reader = open_archive(simple_dir)
    reader.close()
    reader.close()  # should not raise


# ---------------------------------------------------------------------------
# Deep nesting
# ---------------------------------------------------------------------------


def test_deep_nested_structure(deep_dir: Path) -> None:
    with open_archive(deep_dir) as reader:
        names = {m.name for m in reader.members()}
        assert "root.txt" in names
        assert "level1/" in names
        assert "level1/l1.txt" in names
        assert "level1/level2/" in names
        assert "level1/level2/l2.txt" in names


def test_deep_nested_read(deep_dir: Path) -> None:
    with open_archive(deep_dir) as reader:
        data = reader.read("level1/level2/l2.txt")
        assert data == b"level2"


# ---------------------------------------------------------------------------
# extract_all stub
# ---------------------------------------------------------------------------


def test_extract_all_raises_not_implemented(simple_dir: Path, tmp_path: Path) -> None:
    dest = tmp_path / "out"
    dest.mkdir()
    with open_archive(simple_dir) as reader:
        with pytest.raises(NotImplementedError):
            reader.extract_all(dest)


# ---------------------------------------------------------------------------
# Public API: __version__ accessible
# ---------------------------------------------------------------------------


def test_version_accessible() -> None:
    assert archivey.__version__


def test_archive_format_named_instances() -> None:
    fmt = ArchiveFormat.DIRECTORY  # type: ignore[attr-defined]
    assert repr(fmt) == "ArchiveFormat.DIRECTORY"


# ---------------------------------------------------------------------------
# source_name(): names paths and named streams, None for anonymous streams
# ---------------------------------------------------------------------------


def test_source_name_for_path_and_stream(tmp_path: Path) -> None:
    from archivey.core import source_name

    p = tmp_path / "x.bin"
    p.write_bytes(b"")
    assert source_name(p) == str(p)
    assert source_name(str(p)) == str(p)
    with open(p, "rb") as f:
        assert source_name(f) == str(p)
    # An in-memory stream has no name attribute -> None.
    assert source_name(io.BytesIO(b"")) is None


def test_password_rejected(simple_dir: Path) -> None:
    # Directories carry no encryption; a password is API misuse, rejected like the
    # other unencrypted formats rather than silently ignored.
    from archivey.exceptions import UnsupportedOperationError

    with pytest.raises(UnsupportedOperationError):
        open_archive(simple_dir, password="x")
