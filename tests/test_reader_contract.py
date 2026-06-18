"""Contract tests for the BaseArchiveReader extension points.

These exercise the backend contract directly with a minimal in-test reader, independent
of any real format backend — in particular that ``_SUPPORTS_RANDOM_ACCESS = False`` is
actually enforced (it is the forward-only/streaming case the real backends land in later
phases).
"""

from __future__ import annotations

import io
from typing import BinaryIO, Iterator

import pytest

import archivey
from archivey.internal.intent import (
    AccessCost,
    CostReceipt,
    Intent,
    ListingCost,
    StreamCapability,
)
from archivey.internal.reader import BaseArchiveReader
from archivey.internal.types import (
    ArchiveFormat,
    ArchiveInfo,
    ArchiveMember,
    MemberType,
)


class _ForwardOnlyReader(BaseArchiveReader):
    """A reader that cannot do random access — like a non-seekable TAR."""

    _SUPPORTS_RANDOM_ACCESS = False
    _MEMBER_LIST_UPFRONT = False

    def _iter_members(self) -> Iterator[ArchiveMember]:
        yield ArchiveMember(type=MemberType.FILE, name="a.txt", size=1)

    def _open_member(self, member: ArchiveMember) -> BinaryIO:
        return io.BytesIO(b"x")

    def _iter_with_data(self) -> Iterator[tuple[ArchiveMember, BinaryIO | None]]:
        # Forward-only backends MUST override this: yield progressively, never
        # pre-register the whole member list.
        for member in self._iter_members():
            yield member, (io.BytesIO(b"x") if member.is_file else None)

    def _get_archive_info(self) -> ArchiveInfo:
        return ArchiveInfo(
            format=ArchiveFormat.TAR,
            format_version=None,
            is_solid=False,
            member_count=None,
            comment=None,
            is_encrypted=False,
            is_multivolume=False,
            cost=CostReceipt(
                listing_cost=ListingCost.REQUIRES_SCANNING,
                access_cost=AccessCost.DIRECT,
                stream_capability=StreamCapability.FORWARD_ONLY,
            ),
        )

    def _close_archive(self) -> None:
        pass


def _make() -> _ForwardOnlyReader:
    return _ForwardOnlyReader(ArchiveFormat.TAR, Intent.SEQUENTIAL, "x.tar")


def test_open_and_read_raise_without_random_access() -> None:
    reader = _make()
    with pytest.raises(archivey.UnsupportedOperationError):
        reader.open("a.txt")
    with pytest.raises(archivey.UnsupportedOperationError):
        reader.read("a.txt")


def test_stream_members_works_without_random_access() -> None:
    reader = _make()
    out = [(m.name, s.read() if s is not None else None) for m, s in reader.stream_members()]
    assert out == [("a.txt", b"x")]
