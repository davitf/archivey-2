"""Retained metadata-byte accounting for :class:`~archivey.config.ListingLimits`."""

from __future__ import annotations

from typing import Any

from archivey.config import ListingLimits
from archivey.exceptions import ResourceLimitError
from archivey.types import ArchiveMember


def _str_retained_bytes(value: str) -> int:
    """Cheap upper bound on UTF-8 size for listing bomb accounting.

    ``max_metadata_bytes`` is a safety cap, not a precise allocator — encoding
    every field on the open+list hot path is wasted work. UTF-8 is at most 4
    bytes per Unicode code point, so ``4 * len(s)`` never under-counts a
    Unicode name bomb; ASCII uses ``len(s)`` (exact, the common case).
    """
    n = len(value)
    return n if value.isascii() else n * 4


def _extra_bytes(extra: dict[str, Any]) -> int:
    """Sum retained ``str``/``bytes`` lengths in ``extra`` (one-level nested dicts)."""
    total = 0
    for value in extra.values():
        if isinstance(value, str):
            total += _str_retained_bytes(value)
        elif isinstance(value, bytes):
            total += len(value)
        elif isinstance(value, dict):
            for nested in value.values():
                if isinstance(nested, str):
                    total += _str_retained_bytes(nested)
                elif isinstance(nested, bytes):
                    total += len(nested)
    return total


def member_metadata_bytes(member: ArchiveMember) -> int:
    """Retained metadata weight for one member (listing bomb accounting)."""
    # Hot path for ZIP/TAR listing: most optional string fields are None.
    total = _str_retained_bytes(member.name)
    if member.raw_name is not None:
        total += len(member.raw_name)
    comment = member.comment
    if comment is not None:
        total += _str_retained_bytes(comment)
    link_target = member.link_target
    if link_target is not None:
        total += _str_retained_bytes(link_target)
    uname = member.uname
    if uname is not None:
        total += _str_retained_bytes(uname)
    gname = member.gname
    if gname is not None:
        total += _str_retained_bytes(gname)
    if member.extra:
        total += _extra_bytes(member.extra)
    return total


def archive_comment_bytes(comment: str | None) -> int:
    if comment is None:
        return 0
    return _str_retained_bytes(comment)


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
