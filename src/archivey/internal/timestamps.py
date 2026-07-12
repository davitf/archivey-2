"""Shared timestamp helpers for format backends.

The NTFS FILETIME conversion (100 ns ticks since 1601-01-01 UTC → ``datetime``) is used by
every backend that reads Windows-origin timestamps — ZIP's NTFS extra field and the native
7z reader today, RAR later — so it lives here rather than being copy-pasted per backend. The
out-of-range guard is the load-bearing part: ``datetime.fromtimestamp`` raises ``ValueError``/
``OverflowError`` on POSIX but ``OSError`` on Windows for negative/huge inputs, and a hostile
FILETIME must degrade to ``None`` + a reported issue, never sink the whole listing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

# Seconds between the NTFS FILETIME epoch (1601-01-01) and the Unix epoch (1970-01-01).
NTFS_EPOCH_OFFSET = 11_644_473_600


@dataclass(frozen=True)
class TimestampIssue:
    """A non-fatal timestamp-decode problem, surfaced as a ``MEMBER_TIMESTAMP_INVALID`` diagnostic.

    ``source`` names the field family (``"ntfs"``, ``"dos"``, ``"tar"``, …) so a backend that
    reads several timestamp representations (ZIP: DOS + NTFS + extended) can tag each.
    """

    field: str
    source: str
    value_repr: str
    message: str


def filetime_to_datetime(
    value: int | None, filename: str, *, field: str, source: str = "ntfs"
) -> tuple[datetime | None, TimestampIssue | None]:
    """An NTFS FILETIME (100 ns ticks since 1601 UTC) as a datetime; 0/None means "unset".

    Returns ``(datetime, None)`` on success, ``(None, None)`` when unset, and
    ``(None, TimestampIssue)`` for an out-of-range value (which must not fail the listing).
    """
    if value is None or value == 0:
        return None, None
    try:
        return (
            datetime.fromtimestamp(
                value / 10_000_000 - NTFS_EPOCH_OFFSET, tz=timezone.utc
            ),
            None,
        )
    except (ValueError, OverflowError, OSError):
        # fromtimestamp rejects out-of-range values with ValueError/OverflowError, and on
        # some platforms (notably Windows) with OSError for negative/huge inputs.
        return None, TimestampIssue(
            field=field,
            source=source,
            value_repr=repr(value),
            message=f"Invalid NTFS timestamp for {filename!r}: {value!r}",
        )
