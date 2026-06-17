"""BaseArchiveReader ABC and ReadBackend/WriteBackend ABCs."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO, Callable, Iterator

from archivey.internal.errors import (
    ArchiveyError,
    LinkTargetNotFoundError,
    UnsupportedOperationError,
)
from archivey.internal.intent import CostReceipt, Intent
from archivey.internal.types import (
    ArchiveFormat,
    ArchiveInfo,
    ArchiveMember,
    MemberType,
)

# Type alias for member selector filter
MemberSelector = Callable[[ArchiveMember], bool] | None


class ReadBackend(ABC):
    """Stateless factory for creating ArchiveReader instances."""

    FORMATS: tuple[ArchiveFormat, ...]
    EXTENSIONS: tuple[str, ...]
    MAGIC: tuple[tuple[int, bytes], ...]
    REQUIRES_SEEK: bool = False
    OPTIONAL_DEPENDENCY: str | None = None

    @abstractmethod
    def open_read(
        self,
        source: Path | BinaryIO,
        intent: Intent,
        password: bytes | None,
        encoding: str | None,
    ) -> "BaseArchiveReader": ...


class WriteBackend(ABC):
    """Stateless factory for creating ArchiveWriter instances."""

    FORMATS: tuple[ArchiveFormat, ...]
    OPTIONAL_DEPENDENCY: str | None = None

    @abstractmethod
    def open_write(
        self,
        dest: Path | BinaryIO,
        compression: object | None,
        password: bytes | None,
        encoding: str | None,
    ) -> "ArchiveWriter": ...


class ArchiveWriter(ABC):
    """Abstract base for archive writers. Defined here as a placeholder."""


class BaseArchiveReader(ABC):
    """Abstract base class for all archive readers."""

    _SUPPORTS_RANDOM_ACCESS: bool = True
    _MEMBER_LIST_UPFRONT: bool = True

    def __init__(
        self,
        format: ArchiveFormat,
        intent: Intent,
        archive_name: str | None,
    ) -> None:
        self._format = format
        self._intent = intent
        self._archive_name = archive_name
        self._archive_id = str(id(self))
        self._members_cache: list[ArchiveMember] | None = None
        self._members_by_name: dict[str, ArchiveMember] | None = None
        self._closed = False

    @abstractmethod
    def _iter_members(self) -> Iterator[ArchiveMember]: ...

    def _iter_with_data(self) -> Iterator[tuple[ArchiveMember, BinaryIO | None]]:
        """Yield (member, stream) pairs in archive order; backs ``stream_members``.

        This default is for **random-access / fully-indexed** backends only (ZIP,
        directory): it calls ``_get_members_registered()``, which eagerly drains
        ``_iter_members()`` and builds the name map *before* yielding anything.

        Streaming / forward-only / solid backends **must override** this — it is a
        correctness requirement, not just an optimization. A non-seekable TAR or a solid
        7z/RAR cannot enumerate every member before reading data, so the override must
        produce ``(member, stream)`` pairs progressively from a single forward pass and
        must **not** call ``_get_members_registered()``. The yielded stream is only valid
        until the iterator advances (see the ``stream_members`` contract in
        ``archive-reading``); for non-file members it is ``None``.
        """
        for member in self._get_members_registered():
            if member.is_file:
                yield member, self._open_member(member)
            else:
                yield member, None

    @abstractmethod
    def _open_member(self, member: ArchiveMember) -> BinaryIO: ...

    @abstractmethod
    def _get_archive_info(self) -> ArchiveInfo: ...

    @abstractmethod
    def _close_archive(self) -> None: ...

    def _get_members_registered(self) -> list[ArchiveMember]:
        """Get all members, assigning member_id and resolving links."""
        if self._members_cache is not None:
            return self._members_cache

        members: list[ArchiveMember] = []
        for idx, member in enumerate(self._iter_members()):
            member._member_id = idx
            member._archive_id = self._archive_id
            members.append(member)

        # Build name lookup for link resolution
        by_name: dict[str, ArchiveMember] = {m.name: m for m in members}

        # Resolve symlinks/hardlinks
        for member in members:
            if member.type in (MemberType.SYMLINK, MemberType.HARDLINK) and member.link_target:
                self._resolve_link(member, by_name)

        self._members_cache = members
        self._members_by_name = by_name
        return members

    def _resolve_link(
        self,
        member: ArchiveMember,
        by_name: dict[str, ArchiveMember],
    ) -> None:
        """Resolve link_target to link_target_member using cycle detection."""
        visited: set[str] = set()
        current = member

        while current.link_target is not None:
            target_name = current.link_target
            if target_name in visited:
                # Cycle detected; leave link_target_member unset (None) rather than
                # pointing at an intermediate link in the cycle.
                return
            visited.add(current.name)
            target = by_name.get(target_name)
            if target is None:
                # Missing target - leave link_target_member as None
                return
            current = target

        # Set on the original member (the final resolved target or None on cycle)
        if current is not member:
            member.link_target_member = current

    # --- Public API ---

    @property
    def format(self) -> ArchiveFormat:
        return self._format

    @property
    def info(self) -> ArchiveInfo:
        return self._get_archive_info()

    @property
    def cost(self) -> CostReceipt:
        return self._get_archive_info().cost

    def __iter__(self) -> Iterator[ArchiveMember]:
        if self._intent == Intent.SEQUENTIAL and self._members_cache is None:
            # For sequential intent, stream directly without caching
            yield from self._iter_members()
        else:
            yield from self._get_members_registered()

    def members(self) -> list[ArchiveMember]:
        if self._intent == Intent.SEQUENTIAL and not self._MEMBER_LIST_UPFRONT:
            raise UnsupportedOperationError(
                "Cannot materialize member list on a SEQUENTIAL reader without upfront index",
            )
        return list(self._get_members_registered())

    def __len__(self) -> int:
        if self._intent == Intent.SEQUENTIAL and not self._MEMBER_LIST_UPFRONT:
            raise UnsupportedOperationError(
                "Cannot get member count on a SEQUENTIAL reader without upfront index",
            )
        return len(self._get_members_registered())

    def __contains__(self, name: object) -> bool:
        if not isinstance(name, str):
            return False
        self._get_members_registered()
        assert self._members_by_name is not None
        return name in self._members_by_name

    def __getitem__(self, name: str) -> ArchiveMember:
        if self._intent == Intent.SEQUENTIAL and not self._MEMBER_LIST_UPFRONT:
            raise UnsupportedOperationError(
                "Cannot do key lookup on a SEQUENTIAL reader without upfront index",
            )
        self._get_members_registered()
        assert self._members_by_name is not None
        try:
            return self._members_by_name[name]
        except KeyError:
            raise KeyError(f"Member {name!r} not found") from None

    def get(self, name: str, default: ArchiveMember | None = None) -> ArchiveMember | None:
        if self._intent == Intent.SEQUENTIAL and not self._MEMBER_LIST_UPFRONT:
            raise UnsupportedOperationError(
                "Cannot do key lookup on a SEQUENTIAL reader without upfront index",
            )
        self._get_members_registered()
        assert self._members_by_name is not None
        return self._members_by_name.get(name, default)

    def open(self, member: str | ArchiveMember) -> BinaryIO:
        """Open member for reading. Follows symlinks."""
        if isinstance(member, str):
            member = self[member]
        return self._open_with_link_follow(member, visited=set())

    def _open_with_link_follow(
        self,
        member: ArchiveMember,
        visited: set[str],
    ) -> BinaryIO:
        if member.type in (MemberType.SYMLINK, MemberType.HARDLINK):
            if member.name in visited:
                raise LinkTargetNotFoundError(
                    f"Symlink cycle detected involving {member.name!r}",
                    member_name=member.name,
                )
            visited.add(member.name)
            if member.link_target_member is not None:
                return self._open_with_link_follow(member.link_target_member, visited)
            if member.link_target is None:
                raise LinkTargetNotFoundError(
                    f"Link target for {member.name!r} is unknown",
                    member_name=member.name,
                )
            target = (
                self._members_by_name.get(member.link_target)
                if self._members_by_name
                else None
            )
            if target is None:
                raise LinkTargetNotFoundError(
                    f"Link target {member.link_target!r} not found in archive",
                    member_name=member.name,
                )
            return self._open_with_link_follow(target, visited)
        return self._open_member(member)

    def read(self, member: str | ArchiveMember) -> bytes:
        """Read member data as bytes."""
        with self.open(member) as f:
            return f.read()

    def stream_members(
        self,
        members: MemberSelector = None,
    ) -> Iterator[tuple[ArchiveMember, BinaryIO | None]]:
        """Yield (member, stream) pairs. members is a selector filter (no transform)."""
        for m, stream in self._iter_with_data():
            if members is None or members(m):
                yield m, stream
            elif stream is not None:
                stream.close()

    def extract_all(self, dest: str | Path) -> None:
        """Extract all members to dest. (Coordinator implementation deferred to Phase 4.)"""
        raise NotImplementedError("extract_all is deferred to Phase 4")

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._close_archive()

    def __enter__(self) -> "BaseArchiveReader":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _stamp_error_context(
        self, exc: ArchiveyError, member_name: str | None = None
    ) -> None:
        """Stamp format/archive/member context onto an ArchiveyError if not already set."""
        if exc.source_format is None:
            exc.source_format = self._format
        if exc.archive_name is None:
            exc.archive_name = self._archive_name
        if exc.member_name is None and member_name is not None:
            exc.member_name = member_name


# ArchiveReader is the public alias for BaseArchiveReader
ArchiveReader = BaseArchiveReader
