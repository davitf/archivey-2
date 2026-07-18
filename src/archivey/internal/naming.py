"""Member-name normalization and link-target name resolution.

Implements the ``ArchiveMember.name`` normalization rules from the
``archive-data-model`` spec. Format backends call :func:`normalize_member_name`
when decoding a stored name so that every backend produces names under the same
deterministic rules, while keeping the verbatim bytes in ``ArchiveMember.raw_name``.
:func:`resolve_link_target_name` maps a link member's stored target string to the
archive-namespace name it refers to (see ``archive-reading``: link following).
"""

from __future__ import annotations

import os
import posixpath
import re
from collections.abc import Collection
from typing import TYPE_CHECKING

from archivey.diagnostics import (
    DiagnosticCode,
    NameNormalizationContext,
    raw_name_to_base64,
)
from archivey.internal.logs import normalization as logger
from archivey.types import MemberType

if TYPE_CHECKING:
    from archivey.internal.diagnostics_collector import DiagnosticCollector
    from archivey.types import ArchiveMember

# Unicode bidi formatting controls can make the displayed order of a filename differ
# materially from its stored order (for example, disguising an executable suffix).
_BIDI_CONTROLS = frozenset(
    "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
)


def _warn_for_bidirectional_controls(name: str) -> None:
    """Warn once when a presented member name contains a bidi formatting control.

    Called by ``BaseArchiveReader`` while assigning member identity, not by individual
    decoders, so directory and inferred single-file names receive the same warning and a
    backend that uses :func:`normalize_member_name` cannot emit a duplicate.

    ASCII names cannot contain bidi controls — skip the per-character scan (listing
    hot path; perf review H3 / Q1).
    """
    if name.isascii():
        return
    if any(char in _BIDI_CONTROLS for char in name):
        logger.warning(
            "Member name contains a bidirectional control character: %r", name
        )


def infer_member_name_from_archive(
    archive_name: str | None,
    *,
    strip_suffixes: Collection[str] = (),
    strip_suffix_re: re.Pattern[str] | None = None,
) -> str:
    """Infer a presented member name from the archive source path.

    Shared by single-file compressors and nameless 7z members (see
    ``format-single-file-compressors`` / ``format-7z``):

    - No usable archive filename → ``\"data\"``.
    - Basename matches ``strip_suffix_re`` or ends with a ``strip_suffixes`` entry
      (case-insensitive; longest match wins) → remaining stem.
    - Otherwise → ``basename + \".uncompressed\"``.
    """
    if archive_name is None:
        return "data"
    base = os.path.basename(archive_name.rstrip("/"))
    if not base or base in {".", ".."}:
        return "data"

    if strip_suffix_re is not None:
        stem = strip_suffix_re.sub("", base)
        if stem and stem != base:
            return stem

    lower = base.lower()
    best: str | None = None
    best_len = -1
    for suffix in strip_suffixes:
        if not suffix:
            continue
        suf = suffix if suffix.startswith(".") else f".{suffix}"
        if lower.endswith(suf.lower()) and len(suf) > best_len and len(base) > len(suf):
            best = base[: -len(suf)]
            best_len = len(suf)
    if best:
        return best

    return base + ".uncompressed"


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

    When normalization changes the presented path, callers should emit
    :func:`emit_member_name_normalized` once the :class:`~archivey.types.ArchiveMember`
    exists (member-eligible diagnostic).
    """
    name = decoded

    # 1. Backslashes -> forward slashes, only when the format/entry treats them as separators.
    if backslash_is_separator and "\\" in name:
        name = name.replace("\\", "/")

    # Fast path: already a clean relative path — no absolute prefix, no empty/`.`
    # segments to drop. ``..`` is retained as-is under the rules below, so a path
    # that only contains ordinary segments (and optional ``..``) needs no rebuild.
    # Listing hot path (ZIP/TAR open+list); keep behaviour identical to the full walk.
    if (
        name
        and not name.startswith("/")
        and "//" not in name
        and not name.startswith("./")
        and "/./" not in name
        and not name.endswith("/.")
        and name != "."
    ):
        if member_type == MemberType.DIRECTORY and not name.endswith("/"):
            return name + "/"
        return name

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

    return name


def emit_member_name_normalized(
    collector: DiagnosticCollector,
    *,
    member: ArchiveMember,
    presented_name: str,
    archive_name: str | None = None,
) -> None:
    """Emit ``MEMBER_NAME_NORMALIZED`` when normalization changed ``presented_name``.

    Suppresses the no-op case where a DIRECTORY member only gained the canonical
    trailing slash (Python's ``tarfile`` strips it on read) — that is not an
    observable override, and warning once per directory on every ordinary tar is
    noise (R3 / Brief 4).
    """
    if member.name == presented_name:
        return
    if (
        member.type is MemberType.DIRECTORY
        and presented_name + "/" == member.name
        and not presented_name.endswith("/")
    ):
        return
    message = f"Member name normalized: {presented_name!r} -> {member.name!r}"
    collector.emit(
        code=DiagnosticCode.MEMBER_NAME_NORMALIZED,
        message=message,
        context=NameNormalizationContext(
            archive_name=archive_name,
            member_name=member.name,
            member_id=member._member_id,
            raw_name_base64=raw_name_to_base64(member.raw_name),
            presented_name=presented_name,
            normalized_name=member.name,
        ),
        member=member,
        attach_to_member=True,
        logger=logger,
    )


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

    A backslash in ``target`` is a literal character, exactly as in member names: the
    backend that decoded the member already converted ``\\`` to ``/`` where the source
    format/entry treats it as a separator (see :func:`normalize_member_name`'s
    ``backslash_is_separator``), and applies the same rule to the link target it stores.
    Converting unconditionally here would corrupt a POSIX-origin target that legitimately
    contains a backslash.
    """
    if not target:
        return None
    if member_type == MemberType.SYMLINK:
        if target.startswith("/"):
            return None  # absolute: outside the archive namespace
        base_dir = posixpath.dirname(link_name.rstrip("/"))
        joined = posixpath.join(base_dir, target)
    else:
        joined = target
    resolved = posixpath.normpath(joined)
    if resolved in (".", "/") or resolved.startswith(("../", "/")) or resolved == "..":
        return None  # escapes the archive root (or names the root itself)
    return resolved
