"""Contract tests for the BaseArchiveReader extension points and access-mode enforcement.

Exercised directly with minimal in-test readers (no real format backend), covering the
two orthogonal gates — the access mode (``streaming=True`` is forward-only) and backend
**capability** (``_SUPPORTS_RANDOM_ACCESS``) — plus ``get_members_if_available``.
"""

from __future__ import annotations

import io
from typing import BinaryIO, Iterator

import pytest

import archivey
from archivey.cost import (
    AccessCost,
    CostReceipt,
    ListingCost,
    StreamCapability,
)
from archivey.internal.base_reader import BaseArchiveReader
from archivey.types import (
    ArchiveFormat,
    ArchiveInfo,
    ArchiveMember,
    MemberType,
)


def _info(format: ArchiveFormat, listing: ListingCost, stream: StreamCapability) -> ArchiveInfo:
    return ArchiveInfo(
        format=format,
        format_version=None,
        is_solid=False,
        member_count=None,
        comment=None,
        is_encrypted=False,
        is_multivolume=False,
        cost=CostReceipt(
            listing_cost=listing, access_cost=AccessCost.DIRECT, stream_capability=stream
        ),
    )


class _IndexedReader(BaseArchiveReader):
    """Random-access reader with a true upfront index (like ZIP / a directory)."""

    _SUPPORTS_RANDOM_ACCESS = True
    _MEMBER_LIST_UPFRONT = True

    def _iter_members(self) -> Iterator[ArchiveMember]:
        yield ArchiveMember(type=MemberType.FILE, name="a.txt", size=1)

    def _open_member(self, member: ArchiveMember) -> BinaryIO:
        return io.BytesIO(b"x")

    def _get_archive_info(self) -> ArchiveInfo:
        return _info(ArchiveFormat.ZIP, ListingCost.INDEXED, StreamCapability.SEEKABLE)

    def _close_archive(self) -> None:
        pass


class _ForwardOnlyReader(BaseArchiveReader):
    """A reader that cannot do random access and has no upfront index (like a
    non-seekable streaming TAR)."""

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
        return _info(
            ArchiveFormat.TAR, ListingCost.REQUIRES_SCANNING, StreamCapability.FORWARD_ONLY
        )

    def _close_archive(self) -> None:
        pass


# --- Capability gate: _SUPPORTS_RANDOM_ACCESS (independent of access mode) -----------


def test_open_raises_without_random_access_capability() -> None:
    # streaming=False isolates the *capability* gate from the access-mode gate.
    reader = _ForwardOnlyReader(ArchiveFormat.TAR, False, "x.tar")
    with pytest.raises(archivey.UnsupportedOperationError):
        reader.open("a.txt")
    with pytest.raises(archivey.UnsupportedOperationError):
        reader.read("a.txt")


def test_stream_members_works_without_random_access() -> None:
    reader = _ForwardOnlyReader(ArchiveFormat.TAR, False, "x.tar")
    out = [(m.name, s.read() if s is not None else None) for m, s in reader.stream_members()]
    assert out == [("a.txt", b"x")]


# --- Access-mode gate: streaming=True is forward-only, even on a capable backend ----


def test_streaming_disables_random_access_on_capable_backend() -> None:
    reader = _IndexedReader(ArchiveFormat.ZIP, True, "x.zip")  # streaming=True
    for call in (
        lambda: reader.members(),
        lambda: "a.txt" in reader,
        lambda: reader["a.txt"],
        lambda: reader.get("a.txt"),
        lambda: reader.open("a.txt"),
        lambda: reader.read("a.txt"),
    ):
        with pytest.raises(archivey.UnsupportedOperationError):
            call()
    # A single forward pass is still allowed.
    assert [m.name for m in reader] == ["a.txt"]


def test_streaming_len_raises_typeerror_so_list_works() -> None:
    # len() raises TypeError (not UnsupportedOperationError): list(reader) probes
    # __len__ implicitly via the length-hint protocol, which suppresses only TypeError,
    # so plain list(reader) must still perform the forward pass.
    reader = _IndexedReader(ArchiveFormat.ZIP, True, "x.zip")  # streaming=True
    with pytest.raises(TypeError):
        len(reader)
    assert [m.name for m in list(reader)] == ["a.txt"]


# --- get_members_if_available: never scans; safe on any reader ----------------------


def test_get_members_if_available_returns_list_for_indexed_backend() -> None:
    # Even on a streaming reader: it is a no-scan peek, not random access.
    reader = _IndexedReader(ArchiveFormat.ZIP, True, "x.zip")  # streaming=True
    members = reader.get_members_if_available()
    assert members is not None
    assert [m.name for m in members] == ["a.txt"]


def test_get_members_if_available_is_none_without_index() -> None:
    reader = _ForwardOnlyReader(ArchiveFormat.TAR, True, "x.tar")  # streaming=True
    assert reader.get_members_if_available() is None


def test_get_members_if_available_returns_cache_once_materialized() -> None:
    # No upfront index, but a non-streaming iteration materializes the cache, after
    # which the list is available without a fresh scan.
    reader = _ForwardOnlyReader(ArchiveFormat.TAR, False, "x.tar")  # streaming=False
    assert reader.get_members_if_available() is None
    _ = list(reader)
    assert reader.get_members_if_available() is not None


def test_streaming_iteration_registers_member_ids() -> None:
    # A streaming pass must still stamp identity onto the members it yields (the
    # progressive path bypasses _get_members_registered).
    reader = _IndexedReader(ArchiveFormat.ZIP, True, "x.zip")  # streaming=True
    (member,) = list(reader)
    assert member.member_id == 0
    assert member.archive_id == reader._archive_id
