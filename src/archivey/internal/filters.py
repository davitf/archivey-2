"""Universal path-safety checks and policy permission transforms for extraction.

Two independent stages sit in front of every on-disk write (see ``safe-extraction``):

* :func:`check_universal` — the non-bypassable path/link/special-file constraints,
  enforced under **every** :class:`ExtractionPolicy` (including ``TRUSTED``). Run on the
  **original** member before any transform.
* the policy transforms in :data:`POLICY_TRANSFORMS` — permission/ownership normalization
  applied to a transient copy of the member, selected by the active policy.

The post-``os.symlink`` re-resolution check (the third defense-in-depth layer) lives in
the coordinator, next to the ``os.symlink`` call it guards.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from archivey.exceptions import (
    PathTraversalError,
    SpecialFileError,
    SymlinkEscapeError,
)
from archivey.internal.extraction_types import ExtractionPolicy
from archivey.types import ArchiveMember, MemberType

# Split a member name into path components on either separator; a ".." component after
# this split is a traversal attempt regardless of which separator the archive used.
_SEP_SPLIT = re.compile(r"[\\/]")


def _is_absolute(name: str) -> bool:
    """Whether ``name`` is an absolute path: a POSIX root, a UNC share, or a drive letter."""
    if name.startswith("/") or name.startswith("\\"):
        return True  # POSIX root or UNC / rooted-backslash
    # Drive letter: a single ASCII letter followed by ':' (e.g. "C:\\", "C:foo").
    return len(name) >= 2 and name[0].isalpha() and name[1] == ":"


def _within(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def check_universal(member: ArchiveMember, dest: Path) -> None:
    """Enforce the non-bypassable universal path-safety constraints on ``member``.

    ``dest`` is the extraction root. Raises a :class:`FilterRejectionError` subclass
    (``PathTraversalError`` / ``SymlinkEscapeError`` / ``SpecialFileError``) on the first
    violation; returns ``None`` when the member is safe to extract. Applied to the
    original member, before any policy transform, regardless of the active policy.
    """
    name = member.name

    # (1) String checks on member.name. Names are faithful (meaning-preserving
    # normalization keeps a leading "/" and every ".."), so the danger is visible directly
    # on member.name — no separate raw_name inspection is needed. Any ".." component is
    # rejected (escaping and internal alike): a well-formed archive has no reason to carry
    # one. (A future opt-in SANITIZE policy may re-root such names instead of rejecting.)
    if "\x00" in name:
        raise PathTraversalError(
            f"Null byte in member name: {name!r}", member_name=name
        )
    if _is_absolute(name):
        raise PathTraversalError(
            f"Absolute path not allowed: {name!r}", member_name=name
        )
    if ".." in _SEP_SPLIT.split(name):
        raise PathTraversalError(
            f"Path traversal ('..') in member name: {name!r}", member_name=name
        )

    if member.type == MemberType.OTHER:
        raise SpecialFileError(
            f"Special file (device/FIFO/socket) not allowed: {name!r}",
            member_name=name,
        )

    # Pre-extraction path computation: the destination's PARENT directory must resolve
    # within the root. We resolve the parent, not the full path — the final component may
    # be a pre-existing (possibly hostile) symlink that the OverwritePolicy will unlink
    # rather than follow, so following it here would wrongly reject a REPLACE. Combined
    # with the no-".." name check above, a parent inside the root guarantees the member
    # lands inside the root. A symlinked *parent* that escapes is still caught.
    dest_root = dest.resolve()
    rel = name.rstrip("/")
    if rel not in ("", "."):  # "" / "." is the root dir member itself
        parent = (dest_root / rel).parent.resolve()
        if not _within(parent, dest_root):
            raise PathTraversalError(
                f"Member {name!r} resolves outside the destination root",
                member_name=name,
            )

    # Link-target escape at planning time (the authoritative symlink check is re-run
    # post-creation in the coordinator). A symlink target is relative to the link's own
    # directory; a hardlink target is archive-root relative. An absolute target makes the
    # join absolute, so it resolves outside dest and is caught here too.
    if member.link_target is not None:
        if member.type == MemberType.SYMLINK:
            link_parent = (dest_root / name).parent
            resolved_target = (link_parent / member.link_target).resolve()
            if not _within(resolved_target, dest_root):
                raise SymlinkEscapeError(
                    f"Symlink target escapes destination: "
                    f"{name!r} -> {member.link_target!r}",
                    member_name=name,
                )
        elif member.type == MemberType.HARDLINK:
            resolved_target = (dest_root / member.link_target).resolve()
            if not _within(resolved_target, dest_root):
                raise SymlinkEscapeError(
                    f"Hardlink target escapes destination: "
                    f"{name!r} -> {member.link_target!r}",
                    member_name=name,
                )


# --- Policy permission transforms (applied to a transient copy) ---------------------

_HIGH_BITS = 0o7000  # setuid | setgid | sticky
_EXEC_BITS = 0o111


def transform_strict(member: ArchiveMember) -> ArchiveMember:
    """STRICT: drop ownership, strip high/execute bits, normalize to 644/755."""
    new: dict[str, object] = {
        "uid": None,
        "gid": None,
        "uname": None,
        "gname": None,
    }
    if member.is_dir:
        new["mode"] = 0o755
    elif member.is_file:
        mode = member.mode
        if mode is None:
            new["mode"] = 0o644
        else:
            mode = (mode & ~_HIGH_BITS) & ~_EXEC_BITS
            new["mode"] = min(mode & 0o666, 0o644)
    return member.replace(**new)


def transform_standard(member: ArchiveMember) -> ArchiveMember:
    """STANDARD: strip setuid/setgid/sticky, keep execute and ownership."""
    new: dict[str, object] = {}
    if member.is_dir:
        new["mode"] = 0o755 if member.mode is None else (member.mode & ~_HIGH_BITS)
    elif member.is_file:
        new["mode"] = 0o644 if member.mode is None else (member.mode & ~_HIGH_BITS)
    return member.replace(**new)


def transform_trusted(member: ArchiveMember) -> ArchiveMember:
    """TRUSTED: apply stored metadata as-is (a fresh copy, no changes)."""
    return member.replace()


POLICY_TRANSFORMS: dict[ExtractionPolicy, Callable[[ArchiveMember], ArchiveMember]] = {
    ExtractionPolicy.STRICT: transform_strict,
    ExtractionPolicy.STANDARD: transform_standard,
    ExtractionPolicy.TRUSTED: transform_trusted,
}
