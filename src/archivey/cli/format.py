"""Shared formatting helpers for CLI output."""

from __future__ import annotations

from datetime import datetime

from archivey.types import ArchiveMember, MemberType

_TYPE_MARK = {
    MemberType.FILE: "f",
    MemberType.DIRECTORY: "d",
    MemberType.SYMLINK: "l",
    MemberType.HARDLINK: "h",
    MemberType.OTHER: "?",
}


def format_mode(mode: int | None) -> str:
    if mode is None:
        return "---------"
    bits = mode & 0o777
    perms = ""
    for who in (6, 3, 0):
        tri = (bits >> who) & 0o7
        perms += "r" if tri & 0o4 else "-"
        perms += "w" if tri & 0o2 else "-"
        perms += "x" if tri & 0o1 else "-"
    return perms


def format_mtime(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    # Naive wall-clock as stored; drop tz for compact display when aware.
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return dt.strftime("%Y-%m-%d %H:%M")


def format_size(size: int | None) -> str:
    if size is None:
        return "-"
    return str(size)


def format_hash_value(value: bytes | int) -> str:
    """Format a stored digest for CLI display (values are ``bytes``; ``int`` accepted)."""
    if isinstance(value, int):
        return f"{value:08x}" if value <= 0xFFFFFFFF else hex(value)
    return value.hex()


def format_member_line(
    member: ArchiveMember,
    *,
    digests: bool = False,
    verbose: bool = False,
) -> str:
    """Layer-1 listing line; optional stored digests and verbose extras."""
    mark = _TYPE_MARK.get(member.type, "?")
    enc = "E" if member.is_encrypted else "-"
    mode = format_mode(member.mode)
    size = format_size(member.size)
    mtime = format_mtime(member.modified)
    name = member.name
    if member.is_link and member.link_target is not None:
        name = f"{name} -> {member.link_target}"

    parts = [f"{mark}{enc}", mode, f"{size:>10}", mtime, name]
    line = "  ".join(parts)

    if digests and member.hashes:
        digest_bits = " ".join(
            f"{algo}={format_hash_value(val)}"
            for algo, val in sorted(member.hashes.items())
        )
        line = f"{line}  [{digest_bits}]"

    if verbose and member.diagnostics:
        diag = "; ".join(
            d.message for d in member.diagnostics if getattr(d, "message", None)
        )
        if diag:
            line = f"{line}  ({diag})"

    return line
