"""Member selection normalization shared by streaming and extraction."""

from __future__ import annotations

from collections.abc import Callable, Collection
from typing import cast

from archivey.types import ArchiveMember


def normalize_member_selector(
    members: Collection[str | ArchiveMember] | Callable[[ArchiveMember], bool] | None,
) -> Callable[[ArchiveMember], bool] | None:
    """Normalize a collection or predicate selector to a predicate."""
    if members is None:
        return None
    if callable(members):
        return cast("Callable[[ArchiveMember], bool]", members)
    collection = cast("Collection[str | ArchiveMember]", members)
    names: set[str] = set()
    identities: set[tuple[str, int]] = set()
    for entry in collection:
        if isinstance(entry, ArchiveMember):
            # Match by (archive_id, member_id) identity. A member that carries no ids
            # (never registered by a reader — e.g. hand-built) is deliberately dropped:
            # it can't correspond to any real member, so it silently matches nothing.
            if entry._archive_id is not None and entry._member_id is not None:
                identities.add((entry._archive_id, entry._member_id))
        else:
            names.add(entry)

    def predicate(member: ArchiveMember) -> bool:
        if member.name in names:
            return True
        if member._archive_id is not None and member._member_id is not None:
            return (member._archive_id, member._member_id) in identities
        return False

    return predicate
