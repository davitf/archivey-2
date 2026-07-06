"""Public read-only archive interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO, Callable, Iterator

from archivey.cost import CostReceipt
from archivey.internal.extraction_types import (
    ExtractionPolicy,
    ExtractionProgress,
    ExtractionResult,
    MemberFilter,
    MemberSelectorArg,
    OnError,
    OverwritePolicy,
)
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
        on a streaming reader (use :meth:`scan_members` or
        :meth:`get_members_if_available` there)."""
        ...

    @abstractmethod
    def scan_members(self) -> list[ArchiveMember]:
        """Return the fully-resolved member list in either access mode.

        In random-access mode this is equivalent to :meth:`members` and does not
        consume the reader. On a streaming reader it finishes the single forward pass
        (running it from the start, or completing an interrupted one) and returns the
        resolved list; it may also be called after a completed pass to return the
        cached list."""
        ...

    @abstractmethod
    def get_members_if_available(self) -> list[ArchiveMember] | None:
        """The full member list if it is available without scanning, else ``None``.
        Never scans or consumes the forward pass, so it is safe to call on any reader
        (including a streaming one)."""
        ...

    @abstractmethod
    def __contains__(self, member: object) -> bool:
        """Whether ``member`` (an :class:`ArchiveMember`) was yielded by *this* reader.

        Identity-based and O(1) — no scan — so it is valid in any access mode; useful to
        disambiguate members when several readers are in play. Name lookup is
        :meth:`get`, and a non-``ArchiveMember`` operand raises ``TypeError`` (this also
        keeps the ``in`` operator from silently falling back to a full iteration, which
        would consume a streaming reader's single forward pass)."""
        ...

    @abstractmethod
    def get(
        self, name: str, default: ArchiveMember | None = None
    ) -> ArchiveMember | None:
        """Look up a member by its normalized name, returning ``default`` if absent.
        This is the name-lookup entry point; :meth:`open`/:meth:`read` also accept a
        name directly. May trigger a scan; on a streaming reader raises
        ``UnsupportedOperationError``. With duplicate member names, returns the last
        (the one a sequential extraction would leave on disk)."""
        ...

    @abstractmethod
    def open(self, member: str | ArchiveMember) -> BinaryIO:
        """Open a member as a binary stream, following symlinks/hardlinks. Accepts a
        member object or a name (an unknown name raises ``KeyError``). The caller is
        responsible for closing the returned stream."""
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
    def extract_all(
        self,
        dest: str | Path,
        *,
        members: MemberSelectorArg = None,
        filter: MemberFilter | None = None,
        policy: ExtractionPolicy = ExtractionPolicy.STRICT,
        overwrite: OverwritePolicy = OverwritePolicy.ERROR,
        on_error: OnError = OnError.STOP,
        on_progress: Callable[[ExtractionProgress], None] | None = None,
        max_extracted_bytes: int = 2 * 2**30,
        max_ratio: float = 1000.0,
        ratio_activation_threshold: int = 5 * 2**20,
        max_entries: int = 1_048_576,
    ) -> list[ExtractionResult]:
        """Extract members to ``dest`` (safe-by-default; see ``safe-extraction``).

        ``members`` selects which members to extract (names/``ArchiveMember``s, or a
        predicate; ``None`` = all). ``filter`` runs after the universal safety checks and
        the ``policy`` transform, and may rename/sanitize a member (return a
        ``.replace()``d copy) or skip it (return ``None``). Returns one
        :class:`~archivey.ExtractionResult` per member processed.
        """
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
