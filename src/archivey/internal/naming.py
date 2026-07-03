"""Member-name normalization and link-target name resolution.

Implements the ``ArchiveMember.name`` normalization rules from the
``archive-data-model`` spec. Format backends call :func:`normalize_member_name`
when decoding a stored name so that every backend produces names under the same
deterministic rules, while keeping the verbatim bytes in ``ArchiveMember.raw_name``.
:func:`resolve_link_target_name` maps a link member's stored target string to the
archive-namespace name it refers to (see ``archive-reading``: link following).
"""

from __future__ import annotations

import posixpath

from archivey.internal.logs import normalization as logger
from archivey.types import MemberType


def normalize_member_name(
    decoded: str, member_type: MemberType, *, backslash_is_separator: bool
) -> str:
    """Normalize a decoded member name using only **meaning-preserving** rules.

    ``member.name`` is a faithful presentation of the stored path: a leading ``/`` and any
    ``..`` component are **retained** (they are rejected at extraction time — see
    ``safe-extraction`` — not silently re-rooted here). Rules, applied in order:

      1. Replace ``\\`` with ``/`` **only when** ``backslash_is_separator`` is true — the
         backend supplies this per the source format/entry (Windows-origin entries convert;
         TAR and other POSIX formats keep ``\\`` as a literal filename character).
      2. Drop ``.`` segments and empty segments (collapsing ``//`` and ``/./``); keep ``..``.
      3. Append ``/`` for directory members if not already present.
      4. Never produce an empty string — an empty name or a bare root becomes ``"."``.

    A warning is emitted via the ``archivey.normalization`` logger when normalization changes
    the presented path.
    """
    name = decoded

    # 1. Backslashes -> forward slashes, only when the format/entry treats them as separators.
    if backslash_is_separator:
        name = name.replace("\\", "/")

    # 2. Meaning-preserving segment clean-up. Preserve a leading "/" (absolute — rejected at
    #    extraction) and every ".." (retained faithfully); drop only "." and empty segments.
    is_absolute = name.startswith("/")
    kept = [part for part in name.split("/") if part not in (".", "")]
    if not kept:
        name = "."  # empty name or a bare root
    else:
        name = "/".join(kept)
        if is_absolute:
            name = "/" + name

    # 3. Trailing "/" for directories.
    if member_type == MemberType.DIRECTORY and name != "." and not name.endswith("/"):
        name = name + "/"

    if name != decoded:
        logger.warning("Member name normalized: %r -> %r", decoded, name)

    return name


def resolve_link_target_name(
    link_name: str, target: str, member_type: MemberType
) -> str | None:
    """The archive-namespace member name a link's stored target refers to, or ``None``
    when the target cannot name a member of this archive.

    The two link kinds store targets in different namespaces:

    - A **hardlink** target is archive-relative from the root (the TAR model: the
      linkname is the earlier member's own stored path), so it is normalized as-is.
    - A **symlink** target is a filesystem path relative to the link's *own directory*
      (``dir/link -> file`` means ``dir/file``), so it is joined to that directory
      before normalization.

    Returns ``None`` for a target that cannot be a member: an absolute symlink target
    (it points outside the archive namespace) or one that ``..``-escapes the archive
    root. The caller looks the result up against normalized member names; directory
    members carry a trailing ``/`` in their names, so lookups should try both forms.
    """
    if not target:
        return None
    normalized_target = target.replace("\\", "/")
    if member_type == MemberType.SYMLINK:
        if normalized_target.startswith("/"):
            return None  # absolute: outside the archive namespace
        base_dir = posixpath.dirname(link_name.rstrip("/"))
        joined = posixpath.join(base_dir, normalized_target)
    else:
        joined = normalized_target
    resolved = posixpath.normpath(joined)
    if resolved in (".", "/") or resolved.startswith(("../", "/")) or resolved == "..":
        return None  # escapes the archive root (or names the root itself)
    return resolved
