"""Member include/exclude filters for CLI verbs (fnmatch)."""

from __future__ import annotations

import fnmatch
from collections.abc import Callable, Sequence

from archivey.types import ArchiveMember


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
        if include_pats and not any(fnmatch.fnmatch(name, p) for p in include_pats):
            return False
        if exclude_pats and any(fnmatch.fnmatch(name, p) for p in exclude_pats):
            return False
        return True

    return matches
