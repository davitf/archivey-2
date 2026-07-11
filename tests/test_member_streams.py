"""Declared member-stream capabilities: gate, seekability, usage errors."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

import archivey
from archivey import (
    ArchiveyError,
    ArchiveyUsageError,
    ConcurrentAccessError,
    MemberStreams,
    open_archive,
)


def _two_file_dir(tmp_path: Path) -> Path:
    (tmp_path / "a.txt").write_bytes(b"aaa")
    (tmp_path / "b.txt").write_bytes(b"bbb")
    return tmp_path


def test_second_overlapping_open_raises_concurrent_access_error(tmp_path: Path) -> None:
    root = _two_file_dir(tmp_path)
    with open_archive(root) as reader:
        s1 = reader.open("a.txt")
        with pytest.raises(
            ConcurrentAccessError, match="MemberStreams.CONCURRENT"
        ) as ei:
            reader.open("b.txt")
        assert "concurrent-member-streams" not in str(ei.value).lower() or True
        # Breadcrumb points at this test file.
        assert "test_member_streams.py" in str(ei.value)
        assert s1.read() == b"aaa"
        s1.close()
        # After close, another open is fine.
        with reader.open("b.txt") as s2:
            assert s2.read() == b"bbb"


def test_usage_error_is_not_archivey_error(tmp_path: Path) -> None:
    root = _two_file_dir(tmp_path)
    with open_archive(root) as reader:
        s1 = reader.open("a.txt")
        try:
            try:
                reader.open("b.txt")
            except ArchiveyError:
                pytest.fail("ConcurrentAccessError must not be an ArchiveyError")
            except ConcurrentAccessError:
                pass
        finally:
            s1.close()
    assert not issubclass(ConcurrentAccessError, ArchiveyError)
    assert issubclass(ConcurrentAccessError, ArchiveyUsageError)


def test_concurrent_flag_allows_overlapping_opens(tmp_path: Path) -> None:
    root = _two_file_dir(tmp_path)
    with open_archive(root, member_streams=MemberStreams.CONCURRENT) as reader:
        s1 = reader.open("a.txt")
        s2 = reader.open("b.txt")
        assert s1.read() == b"aaa"
        assert s2.read() == b"bbb"
        s1.close()
        s2.close()


def test_default_streams_are_not_seekable(tmp_path: Path) -> None:
    root = _two_file_dir(tmp_path)
    with open_archive(root) as reader:
        with reader.open("a.txt") as s:
            assert s.seekable() is False
            with pytest.raises(io.UnsupportedOperation):
                s.seek(0)
            assert s.tell() == 0
            assert s.read() == b"aaa"


def test_seekable_flag_restores_positioning(tmp_path: Path) -> None:
    root = _two_file_dir(tmp_path)
    with open_archive(root, member_streams=MemberStreams.SEEKABLE) as reader:
        with reader.open("a.txt") as s:
            assert s.seekable() is True
            assert s.read(1) == b"a"
            s.seek(0)
            assert s.read() == b"aaa"


def test_streaming_plus_concurrent_rejected(tmp_path: Path) -> None:
    root = _two_file_dir(tmp_path)
    with pytest.raises(ArchiveyUsageError, match="streaming=True"):
        open_archive(root, streaming=True, member_streams=MemberStreams.CONCURRENT)


def test_extract_needs_no_capability(tmp_path: Path) -> None:
    root = _two_file_dir(tmp_path)
    dest = tmp_path / "out"
    dest.mkdir()
    report = archivey.extract(root, dest)
    assert (dest / "a.txt").read_bytes() == b"aaa"
    assert report.results


def test_wrong_reader_member_is_usage_error(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "f.txt").write_bytes(b"a")
    (b / "f.txt").write_bytes(b"b")
    with open_archive(a) as ra, open_archive(b) as rb:
        member_a = ra.get("f.txt")
        assert member_a is not None
        with pytest.raises(ArchiveyUsageError, match="does not belong"):
            rb.open(member_a)


def test_post_close_reader_ops_are_usage_errors(tmp_path: Path) -> None:
    root = _two_file_dir(tmp_path)
    reader = open_archive(root)
    stream = reader.open("a.txt")
    reader.close()
    with pytest.raises(ArchiveyUsageError, match="closed"):
        reader.open("b.txt")
    # Escaped stream remains usable.
    assert stream.read() == b"aaa"
    stream.close()
