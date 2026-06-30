"""Tests for the exception-translation spine wired through ``ArchiveStream``.

A raw decode error raised while reading a member stream must surface as an
``ArchiveyError`` subclass (via the backend's ``_translate_exception``) stamped with
format/archive/member context (via ``_stamp_error_context``). See ``error-handling`` and
Phase-2 task 0.1.
"""

from __future__ import annotations

import io
from typing import BinaryIO, Iterator

import pytest

from archivey.cost import (
    AccessCost,
    CostReceipt,
    ListingCost,
    StreamCapability,
)
from archivey.exceptions import ArchiveyError, CorruptionError
from archivey.internal.base_reader import BaseArchiveReader
from archivey.types import (
    ArchiveFormat,
    ArchiveInfo,
    ArchiveMember,
    MemberType,
)


class _RawDecodeError(Exception):
    """Stands in for a third-party codec library's own exception type."""


class _RaisingStream(io.RawIOBase):
    """A member stream whose read raises a raw (un-translated) library exception."""

    def readable(self) -> bool:
        return True

    def read(self, n: int = -1, /) -> bytes:
        raise _RawDecodeError("boom")


class _TranslatingReader(BaseArchiveReader):
    """A reader that maps ``_RawDecodeError`` to ``CorruptionError`` and wraps its streams."""

    def _iter_members(self) -> Iterator[ArchiveMember]:
        yield ArchiveMember(type=MemberType.FILE, name="member.bin", size=1)

    def _open_member(self, member: ArchiveMember) -> BinaryIO:
        return self._wrap_member_stream(_RaisingStream(), member.name)

    def _translate_exception(self, exc: Exception) -> ArchiveyError | None:
        if isinstance(exc, _RawDecodeError):
            return CorruptionError(f"decode failed: {exc}")
        return None

    def _get_archive_info(self) -> ArchiveInfo:
        return ArchiveInfo(
            format=ArchiveFormat.ZIP,
            format_version=None,
            is_solid=False,
            member_count=None,
            comment=None,
            is_encrypted=False,
            is_multivolume=False,
            cost=CostReceipt(
                listing_cost=ListingCost.INDEXED,
                access_cost=AccessCost.DIRECT,
                stream_capability=StreamCapability.SEEKABLE,
            ),
        )

    def _close_archive(self) -> None:
        pass


def test_raw_decode_error_surfaces_as_stamped_archiveyerror() -> None:
    reader = _TranslatingReader(ArchiveFormat.ZIP, False, "archive.zip")
    with pytest.raises(CorruptionError) as excinfo:
        reader.read("member.bin")

    err = excinfo.value
    assert isinstance(err.__cause__, _RawDecodeError)  # original attached as cause
    assert err.source_format is ArchiveFormat.ZIP  # format stamped
    assert err.archive_name == "archive.zip"  # archive stamped
    assert err.member_name == "member.bin"  # member stamped


def test_untranslated_exception_propagates_unchanged() -> None:
    """A backend that doesn't recognize an exception lets it propagate (no catch-all)."""

    class _Unmapped(_TranslatingReader):
        def _translate_exception(self, exc: Exception) -> ArchiveyError | None:
            return None  # recognizes nothing

    reader = _Unmapped(ArchiveFormat.ZIP, False, "archive.zip")
    with pytest.raises(_RawDecodeError):
        reader.read("member.bin")
