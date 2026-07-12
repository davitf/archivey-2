"""Retained metadata-byte accounting for :class:`~archivey.config.ListingLimits`."""

from __future__ import annotations

from typing import Any

from archivey.config import ListingLimits
from archivey.exceptions import ResourceLimitError
from archivey.types import ArchiveMember

_STR_FIELDS = ("name", "comment", "link_target", "uname", "gname")


def _utf8_len(value: str) -> int:
    return len(value.encode("utf-8", "surrogateescape"))


def _extra_bytes(extra: dict[str, Any]) -> int:
    """Sum retained ``str``/``bytes`` lengths in ``extra`` (one-level nested dicts)."""
    total = 0
    for value in extra.values():
        if isinstance(value, str):
            total += _utf8_len(value)
        elif isinstance(value, bytes):
            total += len(value)
        elif isinstance(value, dict):
            for nested in value.values():
                if isinstance(nested, str):
                    total += _utf8_len(nested)
                elif isinstance(nested, bytes):
                    total += len(nested)
    return total


def member_metadata_bytes(member: ArchiveMember) -> int:
    """Byte length of retained string/bytes fields on one member (design accounting)."""
    total = 0
    for field_name in _STR_FIELDS:
        value = getattr(member, field_name)
        if isinstance(value, str):
            total += _utf8_len(value)
    if member.raw_name is not None:
        total += len(member.raw_name)
    total += _extra_bytes(member.extra)
    return total


def archive_comment_bytes(comment: str | None) -> int:
    if comment is None:
        return 0
    return _utf8_len(comment)


class ListingLimitTracker:
    """Accumulate member count + retained metadata; optionally raise when caps are crossed."""

    def __init__(self, limits: ListingLimits) -> None:
        self._limits = limits
        self.member_count = 0
        self.metadata_bytes = 0
        self._archive_comment_counted = False

    def reset(self) -> None:
        self.member_count = 0
        self.metadata_bytes = 0
        self._archive_comment_counted = False

    def account_archive_comment(
        self, comment: str | None, *, enforce: bool = True
    ) -> None:
        if self._archive_comment_counted:
            return
        self._archive_comment_counted = True
        added = archive_comment_bytes(comment)
        if added <= 0:
            return
        next_bytes = self.metadata_bytes + added
        if enforce:
            self._check_metadata(next_bytes)
        self.metadata_bytes = next_bytes

    def account_member(self, member: ArchiveMember, *, enforce: bool = True) -> None:
        next_count = self.member_count + 1
        added = member_metadata_bytes(member)
        next_bytes = self.metadata_bytes + added
        if enforce:
            self._check_members(next_count)
            self._check_metadata(next_bytes)
        self.member_count = next_count
        self.metadata_bytes = next_bytes

    def assert_within_limits(self) -> None:
        """Re-check accumulated totals (e.g. when returning a previously built cache)."""
        self._check_members(self.member_count)
        self._check_metadata(self.metadata_bytes)

    def _check_members(self, count: int) -> None:
        max_members = self._limits.max_members
        if max_members is not None and count > max_members:
            raise ResourceLimitError(
                f"Listing limit reached: max_members={max_members} "
                f"(registered {count} members)"
            )

    def _check_metadata(self, nbytes: int) -> None:
        max_meta = self._limits.max_metadata_bytes
        if max_meta is not None and nbytes > max_meta:
            raise ResourceLimitError(
                f"Listing limit reached: max_metadata_bytes={max_meta} "
                f"(retained {nbytes} bytes)"
            )
