"""
Utility functions for archivey.
"""

import datetime
import logging
import os
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar, overload

from archivey.types import MemberType

if TYPE_CHECKING:
    import grp
    import pwd
else:
    try:
        import grp
        import pwd
    except ImportError:
        grp = None
        pwd = None


@overload
def decode_bytes_with_fallback(data: None, encodings: list[str]) -> None: ...


@overload
def decode_bytes_with_fallback(data: bytes, encodings: list[str]) -> str: ...


def decode_bytes_with_fallback(data: bytes | None, encodings: list[str]) -> str | None:
    """
    Decode bytes with a list of encodings, falling back to utf-8 if the first encoding fails.
    """
    if data is None:
        return None

    assert isinstance(data, bytes), "Expected bytes for data"

    for encoding in encodings:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue

    logging.warning(f"Failed to decode {data!r}, falling back to utf-8")
    return data.decode("utf-8", errors="replace")


@overload
def str_to_bytes(s: None) -> None: ...


@overload
def str_to_bytes(s: str | bytes) -> bytes: ...


def str_to_bytes(s: str | bytes | None) -> bytes | None:
    if s is None or isinstance(s, bytes):
        return s
    assert isinstance(s, str), f"Expected str, got {type(s)}"
    return s.encode("utf-8")


@overload
def bytes_to_str(b: None) -> None: ...


@overload
def bytes_to_str(b: str | bytes) -> str: ...


@overload
def bytes_to_str(b: str | bytes | None) -> str | None: ...


def bytes_to_str(b: str | bytes | None) -> str | None:
    if b is None or isinstance(b, str):
        return b
    assert isinstance(b, bytes), f"Expected bytes, got {type(b)}"
    return b.decode("utf-8")


T = TypeVar("T")


def ensure_not_none(x: T | None) -> T:
    if x is None:
        raise ValueError("Expected non-None value")
    return x


def platform_supports_setting_symlink_mtime() -> bool:
    return os.utime in os.supports_follow_symlinks


def platform_supports_setting_symlink_permissions() -> bool:
    return os.chmod in os.supports_follow_symlinks


def platform_is_windows() -> bool:
    return sys.platform.startswith("win")


def set_file_mtime(
    full_path: str, mtime: datetime.datetime, file_type: MemberType
) -> bool:
    kwargs: dict[str, Any] = {}
    if file_type == MemberType.HARDLINK:
        return False
    if file_type == MemberType.SYMLINK:
        if os.utime not in os.supports_follow_symlinks:
            return False
        kwargs["follow_symlinks"] = False

    os.utime(
        full_path,
        (mtime.timestamp(), mtime.timestamp()),
        **kwargs,
    )
    return True


def set_file_permissions(
    full_path: str, permissions: int, file_type: MemberType
) -> bool:
    kwargs = {}
    if file_type == MemberType.HARDLINK:
        return False
    if file_type == MemberType.SYMLINK:
        if os.chmod not in os.supports_follow_symlinks:
            return False
        kwargs["follow_symlinks"] = False

    os.chmod(full_path, permissions, **kwargs)
    return True


@dataclass
class OwnershipInfo:
    """Filesystem ownership information."""

    uid: int | None
    gid: int | None
    uname: str | None
    gname: str | None


def get_ownership_from_stat(stat_result: os.stat_result) -> OwnershipInfo:
    """Return ownership info from an ``os.stat_result``.

    Platform-specific modules are imported lazily to avoid errors on unsupported
    systems.
    """
    if not hasattr(os, "getuid"):
        return OwnershipInfo(uid=None, gid=None, uname=None, gname=None)

    uid = stat_result.st_uid
    gid = stat_result.st_gid

    uname: str | None = None
    gname: str | None = None

    if uid is not None:
        uname = pwd.getpwuid(uid).pw_name if pwd else None

    if gid is not None:
        gname = grp.getgrgid(gid).gr_name if grp else None

    return OwnershipInfo(uid=uid, gid=gid, uname=uname, gname=gname)


def get_current_user_and_group() -> OwnershipInfo:
    """Return the current user and group."""
    if not hasattr(os, "getuid"):
        return OwnershipInfo(uid=None, gid=None, uname=None, gname=None)
    return OwnershipInfo(
        uid=os.getuid(),
        gid=os.getgid(),
        uname=pwd.getpwuid(os.getuid()).pw_name if pwd else None,
        gname=grp.getgrgid(os.getgid()).gr_name if grp else None,
    )
