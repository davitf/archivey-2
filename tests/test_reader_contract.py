"""Contract tests for the BaseArchiveReader extension points and access-mode enforcement.

Exercised directly with minimal in-test readers (no real format backend), covering the
two orthogonal gates — the access mode (``streaming=True`` is forward-only) and backend
**capability** (``_SUPPORTS_RANDOM_ACCESS``) — plus ``members_report_if_available``.
"""

from __future__ import annotations

import io
from typing import Iterator

import pytest

import archivey
from archivey.cost import (
    AccessCost,
    CostReceipt,
    ListingCost,
    StreamCapability,
)
from archivey.exceptions import ArchiveyUsageError
from archivey.internal.base_reader import BaseArchiveReader
from archivey.internal.streams.archive_stream import ArchiveStream
from archivey.types import (
    ArchiveFormat,
    ArchiveInfo,
    ArchiveMember,
    MemberType,
)


def _info(
    format: ArchiveFormat, listing: ListingCost, stream: StreamCapability
) -> ArchiveInfo:
    return ArchiveInfo(
        format=format,
        format_version=None,
        is_solid=False,
        member_count=None,
        comment=None,
        is_encrypted=False,
        is_multivolume=False,
        cost=CostReceipt(
            listing_cost=listing,
            access_cost=AccessCost.DIRECT,
            stream_capability=stream,
        ),
    )


class _IndexedReader(BaseArchiveReader):
    """Random-access reader with a true upfront index (like ZIP / a directory)."""

    _SUPPORTS_RANDOM_ACCESS = True
    _MEMBER_LIST_UPFRONT = True

    def _iter_members(self) -> Iterator[ArchiveMember]:
        yield ArchiveMember(type=MemberType.FILE, name="a.txt", size=1)

    def _open_member(self, member: ArchiveMember) -> ArchiveStream:
        return self._wrap_member_stream(io.BytesIO(b"x"), member.name, size=member.size)

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

    def _open_member(self, member: ArchiveMember) -> ArchiveStream:
        return self._wrap_member_stream(io.BytesIO(b"x"), member.name, size=member.size)

    def _get_archive_info(self) -> ArchiveInfo:
        return _info(
            ArchiveFormat.TAR,
            ListingCost.REQUIRES_SCANNING,
            StreamCapability.FORWARD_ONLY,
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
    out = [
        (m.name, s.read() if s is not None else None)
        for m, s in reader.stream_members()
    ]
    assert out == [("a.txt", b"x")]


# --- Access-mode gate: streaming=True is forward-only, even on a capable backend ----


def test_streaming_disables_random_access_on_capable_backend() -> None:
    reader = _IndexedReader(ArchiveFormat.ZIP, True, "x.zip")  # streaming=True
    for call in (
        lambda: reader.members(),
        lambda: reader.get("a.txt"),
        lambda: reader.open("a.txt"),
        lambda: reader.read("a.txt"),
    ):
        with pytest.raises(archivey.UnsupportedOperationError):
            call()
    # A single forward pass is still allowed.
    assert [m.name for m in reader] == ["a.txt"]


def test_no_len_so_list_works_on_streaming_reader() -> None:
    # The reader defines no __len__ (it is not a collection), so len() raises Python's
    # own TypeError — and list(reader), which probes __len__ via the length-hint
    # protocol (suppressing TypeError), performs the plain forward pass.
    reader = _IndexedReader(ArchiveFormat.ZIP, True, "x.zip")  # streaming=True
    with pytest.raises(TypeError):
        len(reader)
    assert [m.name for m in list(reader)] == ["a.txt"]


def test_contains_is_identity_and_mode_free() -> None:
    # Identity membership works even on a streaming reader (no scan involved), and a
    # string operand raises TypeError instead of falling back to iteration.
    reader = _IndexedReader(ArchiveFormat.ZIP, True, "x.zip")  # streaming=True
    (member,) = list(reader)
    assert member in reader
    other = _IndexedReader(ArchiveFormat.ZIP, False, "y.zip")
    assert other.members()[0] not in reader
    with pytest.raises(TypeError):
        "a.txt" in reader  # noqa: B015 - the expression itself must raise


class _OpenCountingReader(_IndexedReader):
    """Counts ``_open_member`` calls to observe stream laziness."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.opens = 0

    def _iter_members(self) -> Iterator[ArchiveMember]:
        yield ArchiveMember(type=MemberType.FILE, name="a.txt", size=1)
        yield ArchiveMember(type=MemberType.FILE, name="b.txt", size=1)

    def _open_member(self, member: ArchiveMember) -> ArchiveStream:
        self.opens += 1
        return self._wrap_member_stream(io.BytesIO(b"x"), member.name, size=member.size)


def test_stream_members_opens_lazily() -> None:
    # The yielded streams open the member's data on first read, not at yield time: a
    # consumer that skips a member (or a selector that filters it) pays nothing for it.
    reader = _OpenCountingReader(ArchiveFormat.ZIP, False, "x.zip")
    for _member, stream in reader.stream_members():
        assert stream is not None
    assert reader.opens == 0  # iterated, never read -> never opened

    reader = _OpenCountingReader(ArchiveFormat.ZIP, False, "x.zip")
    read = {m.name: s.read() for m, s in reader.stream_members(["b.txt"]) if s}
    assert read == {"b.txt": b"x"}
    assert reader.opens == 1  # only the selected member was opened


def test_open_rejects_member_from_another_reader() -> None:
    # The same identity rule as `member in reader`: a member object yielded by a
    # different reader must not open here — without the check it would resolve against
    # the wrong offsets/paths and could silently return the wrong data.
    reader = _IndexedReader(ArchiveFormat.ZIP, False, "x.zip")
    other = _IndexedReader(ArchiveFormat.ZIP, False, "y.zip")
    foreign = other.members()[0]
    with pytest.raises(ArchiveyUsageError, match="does not belong to this reader"):
        reader.open(foreign)
    # The same name opens fine when looked up on the right reader.
    assert reader.read("a.txt") == b"x"


# --- members_report_if_available: never scans; safe on any reader -------------------


def test_members_report_if_available_returns_report_for_indexed_backend() -> None:
    # Even on a streaming reader: it is a no-scan peek, not random access.
    reader = _IndexedReader(ArchiveFormat.ZIP, True, "x.zip")  # streaming=True
    report = reader.members_report_if_available()
    assert report is not None
    assert report.error is None
    assert [m.name for m in report] == ["a.txt"]


def test_members_report_if_available_is_none_without_index() -> None:
    reader = _ForwardOnlyReader(ArchiveFormat.TAR, True, "x.tar")  # streaming=True
    assert reader.members_report_if_available() is None


def test_members_report_if_available_returns_cache_once_materialized() -> None:
    # No upfront index, but a non-streaming iteration materializes the cache, after
    # which the list is available without a fresh scan.
    reader = _ForwardOnlyReader(ArchiveFormat.TAR, False, "x.tar")  # streaming=False
    assert reader.members_report_if_available() is None
    _ = list(reader)
    report = reader.members_report_if_available()
    assert report is not None
    assert report.error is None


def test_streaming_iteration_registers_member_ids() -> None:
    # A streaming pass must still stamp identity onto the members it yields (the
    # progressive path bypasses _get_members_registered).
    reader = _IndexedReader(ArchiveFormat.ZIP, True, "x.zip")  # streaming=True
    (member,) = list(reader)
    assert member.member_id == 0
    assert member.archive_id == reader._archive_id


def test_streaming_second_iter_raises() -> None:
    reader = _IndexedReader(ArchiveFormat.ZIP, True, "x.zip")
    list(reader)
    with pytest.raises(archivey.UnsupportedOperationError):
        list(reader)


def test_members_report_if_available_after_streaming_pass() -> None:
    reader = _ForwardOnlyReader(ArchiveFormat.TAR, True, "x.tar")
    list(reader)
    report = reader.members_report_if_available()
    assert report is not None
    assert report.error is None
    assert [m.name for m in report] == ["a.txt"]


def test_scan_members_equals_members_in_random_mode() -> None:
    reader = _IndexedReader(ArchiveFormat.ZIP, False, "x.zip")
    assert reader.scan_members() == reader.members()
    assert [m.name for m in reader] == ["a.txt"]


# --- BaseException during materialization must not wedge the reader (review C1/Q1) --


class _FlakyReader(_IndexedReader):
    """An indexed reader whose first materialization attempt is interrupted.

    Simulates a KeyboardInterrupt/MemoryError raised mid-scan: the first
    ``_iter_members`` raises BaseException, later ones succeed.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._raised = False

    def _iter_members(self) -> Iterator[ArchiveMember]:
        if not self._raised:
            self._raised = True
            raise KeyboardInterrupt("interrupted mid-scan")
        yield ArchiveMember(type=MemberType.FILE, name="a.txt", size=1)


def test_baseexception_during_materialization_does_not_wedge_reader() -> None:
    reader = _FlakyReader(ArchiveFormat.ZIP, False, "x.zip")
    # The interrupt propagates unchanged (BaseException, not reclassified).
    with pytest.raises(KeyboardInterrupt):
        reader.members()
    # The reader must remain usable: the failed election was rolled back to
    # UNMATERIALIZED, so a retry re-elects and succeeds instead of raising a
    # misleading "materialization already in progress".
    assert [m.name for m in reader.members()] == ["a.txt"]


# --- A failed streaming pass must not publish a partial member list (deep N1) --------


class _FailingScanReader(BaseArchiveReader):
    """Streaming reader whose scan raises after yielding its first member."""

    _SUPPORTS_RANDOM_ACCESS = False
    _MEMBER_LIST_UPFRONT = False

    def _iter_members(self) -> Iterator[ArchiveMember]:
        yield ArchiveMember(type=MemberType.FILE, name="a.txt", size=1)
        raise archivey.CorruptionError("scan failed mid-pass")

    def _open_member(self, member: ArchiveMember) -> ArchiveStream:
        return self._wrap_member_stream(io.BytesIO(b"x"), member.name, size=member.size)

    def _get_archive_info(self) -> ArchiveInfo:
        return _info(
            ArchiveFormat.TAR,
            ListingCost.REQUIRES_SCANNING,
            StreamCapability.FORWARD_ONLY,
        )

    def _close_archive(self) -> None:
        pass


class _FailingRandomAccessReader(_IndexedReader):
    """Random-access reader whose listing ends with terminal archive damage."""

    def _iter_members(self) -> Iterator[ArchiveMember]:
        yield ArchiveMember(type=MemberType.FILE, name="a.txt", size=1)
        raise archivey.CorruptionError("scan failed after prefix")


def test_random_access_terminal_damage_report_and_yield_then_raise() -> None:
    reader = _FailingRandomAccessReader(ArchiveFormat.ZIP, False, "x.zip")

    report = reader.members_report()
    assert [m.name for m in report] == ["a.txt"]
    assert isinstance(report.error, archivey.CorruptionError)
    assert reader.members_report() is report

    with pytest.raises(archivey.CorruptionError):
        reader.members()
    with pytest.raises(archivey.CorruptionError):
        reader.scan_members()

    it = iter(reader)
    member = next(it)
    assert member.name == "a.txt"
    assert reader.read(member) == b"x"
    with pytest.raises(archivey.CorruptionError):
        next(it)

    assert reader.get("a.txt") is member
    with pytest.raises(archivey.CorruptionError):
        reader.get("missing.txt")


def test_failed_streaming_pass_publishes_incomplete_report() -> None:
    # Terminal archive damage publishes the recovered prefix as an incomplete report,
    # never as a complete member list.
    reader = _FailingScanReader(ArchiveFormat.TAR, True, "x.tar")
    it = iter(reader)
    assert next(it).name == "a.txt"
    with pytest.raises(archivey.CorruptionError):
        next(it)
    # Complete-list methods must fail loud, not serve the partial scan as complete.
    with pytest.raises(archivey.CorruptionError):
        reader.scan_members()
    report = reader.members_report_if_available()
    assert report is not None
    assert isinstance(report.error, archivey.CorruptionError)
    assert [m.name for m in report] == ["a.txt"]


def test_failed_streaming_pass_replays_incomplete_report() -> None:
    reader = _FailingScanReader(ArchiveFormat.TAR, True, "x.tar")
    with pytest.raises(archivey.CorruptionError):
        list(reader)
    for _ in range(2):
        with pytest.raises(archivey.CorruptionError):
            reader.scan_members()
        report = reader.members_report()
        assert [m.name for m in report] == ["a.txt"]
        assert isinstance(report.error, archivey.CorruptionError)
