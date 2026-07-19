"""Member include/exclude filters for CLI verbs (fnmatch)."""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, TextIO

from archivey.cost import StreamCapability
from archivey.types import ArchiveMember

if TYPE_CHECKING:
    from archivey.reader import ArchiveReader


def member_predicate(
    includes: Sequence[str] | None,
    excludes: Sequence[str] | None,
) -> Callable[[ArchiveMember], bool] | None:
    """Build a ``members=`` predicate from positional includes and ``--exclude``.

    A member is selected when it matches any include (or none are given) and matches
    no exclude. Returns ``None`` when every member should be processed.
    """
    include_pats = list(includes or ())
    exclude_pats = list(excludes or ())
    if not include_pats and not exclude_pats:
        return None

    def matches(member: ArchiveMember) -> bool:
        name = member.name
        # fnmatchcase: deterministic across platforms (fnmatch is case-folding on Windows).
        if include_pats and not any(fnmatch.fnmatchcase(name, p) for p in include_pats):
            return False
        if exclude_pats and any(fnmatch.fnmatchcase(name, p) for p in exclude_pats):
            return False
        return True

    return matches


def unmatched_include_patterns(
    includes: Sequence[str],
    members: Sequence[ArchiveMember],
) -> list[str]:
    """Return include patterns that match no member names (order preserved)."""
    if not includes:
        return []
    hit = dict.fromkeys(includes, False)
    for member in members:
        name = member.name
        for pattern, matched in hit.items():
            if not matched and fnmatch.fnmatchcase(name, pattern):
                hit[pattern] = True
        if all(hit.values()):
            break
    return [pattern for pattern, matched in hit.items() if not matched]


def warn_unmatched_includes(
    unmatched: Sequence[str],
    *,
    err: TextIO,
    dest_hint: bool = False,
) -> None:
    """Print stderr warnings for unmatched include patterns (cli-product P2 / Q3).

    When ``dest_hint`` is true and there is exactly one unmatched pattern that
    names an existing directory or ends with ``/``, append a ``-d`` suggestion
    (the unzip/7z positional-dest reflex).
    """
    for pattern in unmatched:
        extra = ""
        if (
            dest_hint
            and len(unmatched) == 1
            and (pattern.endswith("/") or os.path.isdir(pattern))
        ):
            # Strip a trailing slash for the suggested -d argument display.
            suggested = pattern.rstrip("/") or pattern
            extra = f" (did you mean -d {suggested}?)"
        print(
            f"warning: pattern matched no members: {pattern!r}{extra}",
            file=err,
        )


def count_selected(
    members: Sequence[ArchiveMember],
    pred: Callable[[ArchiveMember], bool] | None,
) -> int:
    """How many members the include/exclude predicate selects."""
    if pred is None:
        return len(members)
    return sum(1 for member in members if pred(member))


def members_for_include_check(reader: ArchiveReader) -> list[ArchiveMember] | None:
    """Member list for unmatched-include / empty-selection checks, if safe.

    Prefer a cheap index. On a forward-only (streaming) reader, return ``None``
    instead of calling :meth:`~archivey.reader.ArchiveReader.members_report` —
    that would consume the sole forward pass and break a following
    ``extract_all`` / ``stream_members``. Callers then defer empty-selection
    handling to the operation outcome.
    """
    indexed = reader.members_report_if_available()
    if indexed is not None:
        return list(indexed)
    if reader.cost.stream_capability is StreamCapability.FORWARD_ONLY:
        return None
    return list(reader.members_report())
