"""Shared formatting helpers for CLI output."""

from __future__ import annotations

from datetime import datetime

from archivey.types import ArchiveMember, MemberType

_TYPE_MARK = {
    MemberType.FILE: "f",
    MemberType.DIRECTORY: "d",
    MemberType.SYMLINK: "l",
    MemberType.HARDLINK: "h",
    MemberType.ANTI: "A",
    MemberType.OTHER: "?",
}

# C0 controls, DEL, and C1 controls — never emit raw into a terminal.
_CONTROL_ESCAPE = {
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    "\\": "\\\\",
}


def escape_member_name(name: str) -> str:
    """Backslash-escape control bytes in a member name for safe terminal display.

    Archive member names are attacker-controlled; raw ANSI / ``\\r`` would let a
    hostile archive spoof listing lines. GNU ``ls`` / ``tar`` quote for the same
    reason. Style follows the cli-product recommendation (Q4 lean): escape
    everywhere, lossless backslash form, no ``--raw`` yet.
    """
    out: list[str] = []
    for ch in name:
        if ch in _CONTROL_ESCAPE:
            out.append(_CONTROL_ESCAPE[ch])
            continue
        if ch.isprintable():
            out.append(ch)
            continue
        code = ord(ch)
        if 0xDC00 <= code <= 0xDFFF:
            # surrogateescape of a single byte — show the underlying octet.
            out.append(f"\\x{code & 0xFF:02x}")
        elif code <= 0xFF:
            out.append(f"\\x{code:02x}")
        else:
            out.append(f"\\u{code:04x}")
    return "".join(out)


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
    if member.is_encrypted:
        status = "E"
    elif not member.is_current:
        status = "~"  # superseded / non-current revision
    else:
        status = "-"
    mode = format_mode(member.mode)
    size = format_size(member.size)
    mtime = format_mtime(member.modified)
    name = escape_member_name(member.name)
    if member.is_link and member.link_target is not None:
        name = f"{name} -> {escape_member_name(member.link_target)}"

    parts = [f"{mark}{status}", mode, f"{size:>10}", mtime, name]
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
