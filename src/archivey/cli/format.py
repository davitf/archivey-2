"""Shared formatting helpers for CLI output."""

from __future__ import annotations

from datetime import datetime

from archivey.cost import AccessCost, CostReceipt, ListingCost, StreamCapability
from archivey.types import ArchiveFormat, ArchiveMember, MemberType

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


def format_format_label(fmt: ArchiveFormat) -> str:
    """Human format name (``zip``, ``7z``, ``tar.gz``) rather than enum spellings."""
    ext = fmt.file_extension()
    if ext:
        return ext
    return fmt.display_name.lower().replace("_", "-")


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


def format_access_summary(cost: CostReceipt) -> str:
    """One-line human summary of ``CostReceipt`` for ``archivey info`` (Q5 / P14).

    Derived from the public open-time axes only — not accelerator install state
    (that lives in config / diagnostics, not the frozen receipt).
    """
    if cost.access_cost is AccessCost.SOLID:
        bits: list[str] = []
        if cost.listing_cost is ListingCost.REQUIRES_DECOMPRESSION:
            bits.append("listing requires decompression")
        elif cost.listing_cost is ListingCost.REQUIRES_SCANNING:
            bits.append("listing requires scan")
        elif cost.listing_cost is ListingCost.INDEXED:
            bits.append("reading one member may decode earlier members in its block")
        if cost.solid_block_count is not None:
            n = cost.solid_block_count
            bits.append(f"{n} solid block{'s' if n != 1 else ''}")
        if cost.stream_capability is StreamCapability.FORWARD_ONLY:
            bits.append("forward-only source")
        return f"solid ({'; '.join(bits)})" if bits else "solid"

    if cost.listing_cost is ListingCost.INDEXED:
        head = "random (indexed)"
    elif cost.listing_cost is ListingCost.REQUIRES_SCANNING:
        head = "random (listing requires scan)"
    else:
        head = "random (listing requires decompression)"

    if cost.stream_capability is StreamCapability.FORWARD_ONLY:
        return f"{head}; forward-only source"
    return head
