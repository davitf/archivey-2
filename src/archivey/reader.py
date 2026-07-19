"""Public read-only archive interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Collection, Iterator

from archivey.config import ArchiveyConfig, ExtractionLimits
from archivey.cost import CostReceipt
from archivey.diagnostics import DiagnosticSummary, ExtractionReport, MemberListReport
from archivey.internal.extraction_types import (
    ExtractionPolicy,
    ExtractionProgress,
    MemberFilter,
    OnError,
    OverwritePolicy,
)
from archivey.types import ArchiveFormat, ArchiveInfo, ArchiveMember

if TYPE_CHECKING:
    from archivey.internal.streams.archive_stream import ArchiveStream
    from archivey.measurement import IoStats

# Type alias for the member selector passed to stream_members() and extract_all().
# Accepts a predicate, a collection of names / ArchiveMember objects, or None (all).
MemberSelector = (
    Collection[str | ArchiveMember] | Callable[[ArchiveMember], bool] | None
)


class ArchiveReader(ABC):
    """The public, read-only interface to an open archive.

    Returned by :func:`archivey.open_archive`. Annotate against this type; concrete
    machinery lives in the internal ``BaseArchiveReader`` helper. Use in a ``with``
    block.

    **Listing APIs** (easy to mix up):

    - :meth:`members` — complete list or raise; random-access only (fails on streaming).
    - :meth:`members_report` — always returns a report; check ``error is None`` for
      completeness (preferred for damaged archives).
    - :meth:`scan_members` — like ``members``, but also finishes a streaming forward
      pass so you can obtain a full list after a partial iteration.
    - :meth:`members_report_if_available` — never scans; ``None`` if not yet cached.
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

    @property
    @abstractmethod
    def diagnostics(self) -> DiagnosticSummary:
        """Fresh immutable cumulative snapshot of diagnostics for this reader."""
        ...

    @abstractmethod
    def __iter__(self) -> Iterator[ArchiveMember]:
        """Iterate members in archive order (served from cache once materialized)."""
        ...

    @abstractmethod
    def members(self) -> list[ArchiveMember]:
        """All members as a list. May trigger a scan; raises ``UnsupportedOperationError``
        on a streaming reader (use :meth:`scan_members` or
        :meth:`members_report_if_available` there). Raises terminal archive-level listing
        errors instead of returning an incomplete list."""
        ...

    @abstractmethod
    def members_report(self) -> MemberListReport:
        """Materialize the member listing and return a report.

        ``report.error is None`` means ``report.members`` is complete. A non-``None``
        error means the tuple is the recovered prefix and the error is the terminal
        archive-level listing damage. Unlike :meth:`members`, this returns the report
        instead of raising for those terminal archive-damage errors.
        """
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
    def members_report_if_available(self) -> MemberListReport | None:
        """A member-list report if available without scanning, else ``None``.
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
    def open(self, member: str | ArchiveMember) -> ArchiveStream:
        """Open a member as a binary stream, following symlinks/hardlinks. Accepts a
        member object or a name (an unknown name raises ``KeyError``; a member object
        that was not yielded by this reader raises ``ArchiveyUsageError`` — same identity
        rule as ``member in reader``). The caller is responsible for closing the returned
        stream. Returns an :class:`~archivey.ArchiveStream` (usable as ``BinaryIO``)."""
        ...

    @abstractmethod
    def read(self, member: str | ArchiveMember) -> bytes:
        """Read a member's full contents as ``bytes`` (unbounded — prefer :meth:`open`
        or :meth:`stream_members` for anything not known to be small)."""
        ...

    @abstractmethod
    def stream_members(
        self, members: MemberSelector = None
    ) -> Iterator[tuple[ArchiveMember, ArchiveStream | None]]:
        """Yield ``(member, stream)`` pairs in archive order with bounded memory.
        ``members`` is an optional selector (predicate, name/member collection, or
        ``None`` for all). The yielded stream is valid only until the iterator advances;
        it is ``None`` for non-file members."""
        ...

    @abstractmethod
    def extract_all(
        self,
        dest: str | Path,
        *,
        members: MemberSelector = None,
        filter: MemberFilter | None = None,
        policy: ExtractionPolicy = ExtractionPolicy.STRICT,
        overwrite: OverwritePolicy = OverwritePolicy.ERROR,
        on_error: OnError = OnError.STOP,
        on_progress: Callable[[ExtractionProgress], None] | None = None,
        config: ArchiveyConfig | None = None,
        limits: ExtractionLimits | None = None,
    ) -> ExtractionReport:
        """Extract members to ``dest`` (safe-by-default; see ``safe-extraction``).

        ``members`` selects which members to extract (names/``ArchiveMember``s, or a
        predicate; ``None`` = all). ``filter`` runs after the universal safety checks and
        the ``policy`` transform, and may rename/sanitize a member (return a
        ``.replace()``d copy) or skip it (return ``None``). ``config`` defaults to the
        config the reader was opened with; ``limits`` overrides its extraction limits for
        this call only. Returns an :class:`~archivey.ExtractionReport` whose diagnostic
        summary is the delta for this extraction call.
        """
        ...

    @abstractmethod
    def io_stats(self) -> "IoStats | None":
        """Return I/O counters if measurement was enabled at open time, else ``None``.

        Enable via :func:`archivey.measurement.enable_measurement` around the
        :func:`archivey.open_archive` call. Counters cover bytes decompressed, compressed
        bytes consumed from the outer source, and source seek calls.
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
