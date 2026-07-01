"""Public read-only archive interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO, Callable, Iterator

from archivey.cost import CostReceipt
from archivey.types import ArchiveFormat, ArchiveInfo, ArchiveMember

# Type alias for the member selector passed to stream_members(). Only the predicate form
# is implemented today. The archive-reading spec also allows a Collection[ArchiveMember |
# str] form ("yield exactly these names/members"); that lands with the Phase 5 public-API
# finalization, where its semantics under duplicate member names must be decided (match
# by name? by identity?) — see PLAN.md Phase 5.
MemberSelector = Callable[[ArchiveMember], bool] | None


class ArchiveReader(ABC):
    """The public, read-only interface to an open archive.

    This is the type returned by :func:`archivey.open_archive` and the one programs
    should annotate against. It declares **only** the public contract; the concrete
    machinery (and the internal hooks format backends implement) lives in the
    ``BaseArchiveReader`` helper, which every real reader extends. Implements the
    context-manager protocol, so use it in a ``with`` block.
    """

    @property
    @abstractmethod
    def format(self) -> ArchiveFormat:
        """The detected ``(container, stream)`` format of the open archive."""
        ...

    @property
    @abstractmethod
    def info(self) -> ArchiveInfo:
        """Archive-level metadata (format, solidity, counts, encryption, cost)."""
        ...

    @property
    @abstractmethod
    def cost(self) -> CostReceipt:
        """The listing/access cost receipt for this archive (see ``access-mode-and-cost``)."""
        ...

    @abstractmethod
    def __iter__(self) -> Iterator[ArchiveMember]:
        """Iterate members in archive order (served from cache once materialized)."""
        ...

    @abstractmethod
    def members(self) -> list[ArchiveMember]:
        """All members as a list. May trigger a scan; raises ``UnsupportedOperationError``
        on a streaming reader (use :meth:`get_members_if_available` there)."""
        ...

    @abstractmethod
    def get_members_if_available(self) -> list[ArchiveMember] | None:
        """The full member list if it is available without scanning, else ``None``.
        Never scans or consumes the forward pass, so it is safe to call on any reader
        (including a streaming one)."""
        ...

    @abstractmethod
    def __len__(self) -> int:
        """Member count (may trigger a scan). On a streaming reader raises ``TypeError``
        — not ``UnsupportedOperationError`` like :meth:`members` — because ``list(reader)``
        probes ``__len__`` implicitly via the length-hint protocol, which suppresses only
        ``TypeError``; iteration must keep working there."""
        ...

    @abstractmethod
    def __contains__(self, name: object) -> bool:
        """Whether a member with the given name exists."""
        ...

    @abstractmethod
    def __getitem__(self, name: str) -> ArchiveMember:
        """Look up a member by name; raises ``KeyError`` if absent."""
        ...

    @abstractmethod
    def get(
        self, name: str, default: ArchiveMember | None = None
    ) -> ArchiveMember | None:
        """Look up a member by name, returning ``default`` if absent."""
        ...

    @abstractmethod
    def open(self, member: str | ArchiveMember) -> BinaryIO:
        """Open a member as a binary stream, following symlinks/hardlinks. The caller
        is responsible for closing the returned stream."""
        ...

    @abstractmethod
    def read(self, member: str | ArchiveMember) -> bytes:
        """Read a member's full contents as ``bytes`` (unbounded — prefer :meth:`open`
        or :meth:`stream_members` for anything not known to be small)."""
        ...

    @abstractmethod
    def stream_members(
        self, members: MemberSelector = None
    ) -> Iterator[tuple[ArchiveMember, BinaryIO | None]]:
        """Yield ``(member, stream)`` pairs in archive order with bounded memory.
        ``members`` is an optional selector predicate (no transform). The yielded stream
        is valid only until the iterator advances; it is ``None`` for non-file members."""
        ...

    @abstractmethod
    def extract_all(self, dest: str | Path) -> None:
        """Extract all members to ``dest`` (safe-by-default; see ``safe-extraction``)."""
        ...

    @abstractmethod
    def close(self) -> None:
        """Release resources held by the reader. Idempotent; using a reader after
        ``close()`` is undefined."""
        ...

    @abstractmethod
    def __enter__(self) -> "ArchiveReader": ...

    @abstractmethod
    def __exit__(self, *args: object) -> None: ...
