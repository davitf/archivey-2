"""Tests for MemberSelector collection form (Phase 5 stage 4)."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

from archivey import open_archive


def _tar_with_duplicate_names() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for content in (b"first", b"second"):
            info = tarfile.TarInfo("dup.txt")
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def test_stream_members_name_selects_all_duplicates() -> None:
    with open_archive(io.BytesIO(_tar_with_duplicate_names())) as ar:
        selected = [
            (member.name, stream.read() if stream is not None else None)
            for member, stream in ar.stream_members(members=["dup.txt"])
        ]
    assert selected == [("dup.txt", b"first"), ("dup.txt", b"second")]


def test_stream_members_member_selects_by_identity() -> None:
    with open_archive(io.BytesIO(_tar_with_duplicate_names())) as ar:
        first, second = ar.members()
        selected = [
            stream.read() if stream is not None else None
            for _member, stream in ar.stream_members(members=[first])
        ]
    assert selected == [b"first"]
    assert first is not second


def test_stream_members_mixed_collection(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, content in (("keep.txt", b"keep"), ("skip.txt", b"skip")):
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    with open_archive(io.BytesIO(buf.getvalue())) as ar:
        skip = ar.get("skip.txt")
        assert skip is not None
        selected = [
            member.name
            for member, _stream in ar.stream_members(members=["keep.txt", skip])
        ]
    assert selected == ["keep.txt", "skip.txt"]
