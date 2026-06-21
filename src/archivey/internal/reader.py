"""BaseArchiveReader ABC and ReadBackend/WriteBackend ABCs."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO, Callable, Iterator

from archivey.internal.cost import CostReceipt
from archivey.internal.errors import (
    ArchiveyError,
    LinkTargetNotFoundError,
    UnsupportedOperationError,
)
from archivey.internal.streams.archive_stream import ArchiveStream
from archivey.internal.streams.binaryio import is_seekable
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
        streaming: bool,
        password: bytes | None,
        encoding: str | None,
        archive_name: str | None,
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
        """Member count (same constraints as :meth:`members`)."""
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


class BaseArchiveReader(ArchiveReader):
    """Internal helper base for all format readers — the backend contract lives here.

    Implements the public :class:`ArchiveReader` surface (iteration, lookup, link
    following, lifecycle). Format backends extend **this**, not ``ArchiveReader``.

    Implementing a backend
    ----------------------
    **MUST implement** (abstract):

    - ``_iter_members()``       — yield every :class:`ArchiveMember` once, in archive
      order.
    - ``_open_member(member)``  — return a ``BinaryIO`` for a ``FILE`` member's data.
    - ``_get_archive_info()``   — return the :class:`ArchiveInfo` (format, solidity,
      cost, …).
    - ``_close_archive()``      — release resources (called exactly once, via
      ``close()``).

    **MUST set** when they differ from the defaults (both default ``True``):

    - ``_MEMBER_LIST_UPFRONT``    — does the backend have a true upfront index (central
      directory, 7z header, filesystem listing) that yields the full member list
      *without scanning*? This is the predicate behind :meth:`get_members_if_available`
      (it returns the list when ``True``, else ``None``). It does **not** gate the
      access-mode-enforced methods — those key off the ``streaming`` flag alone.
    - ``_SUPPORTS_RANDOM_ACCESS`` — can an arbitrary member be opened out of order?
      When ``False``, ``open``/``read`` raise ``UnsupportedOperationError``; sequential
      access via ``stream_members`` still works. (The open-time fail-fast for a
      non-seekable source under ``streaming=False`` — which also consults this — lands
      with format detection in Phase 3.)

    Access-mode enforcement (independent of the flags above): a ``streaming=True`` reader
    is forward-only, so ``members``/``len``/``__contains__``/``__getitem__``/``get``/
    ``open``/``read`` all raise ``UnsupportedOperationError`` — uniformly, not
    per-backend. Only a single pass of ``__iter__``/``stream_members``/``extract_all``
    (and ``get_members_if_available``) is allowed.

    **MAY override**:

    - ``_iter_with_data()`` — see its own docstring. The default is correct for
      random-access / indexed backends only; **streaming / solid backends MUST override
      it** (correctness, not just efficiency).

    Everything else here (``_get_members_registered``, ``_resolve_link``,
    ``_open_with_link_follow``, ``_stamp_error_context``) is internal plumbing and is not
    an extension point.
    """

    # Can an arbitrary member be opened out of order? When False, open()/read() raise
    # UnsupportedOperationError and callers must use stream_members() instead.
    _SUPPORTS_RANDOM_ACCESS: bool = True
    # Is the full member list available without reading member data (e.g. a central
    # directory)? Drives get_members_if_available(); does not gate the streaming methods.
    _MEMBER_LIST_UPFRONT: bool = True

    def __init__(
        self,
        format: ArchiveFormat,
        streaming: bool,
        archive_name: str | None,
    ) -> None:
        self._format = format
        self._streaming = streaming
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

    def _translate_exception(self, exc: Exception) -> ArchiveyError | None:
        """Map a raw exception (from a codec/library while reading a member) to an
        ``ArchiveyError`` subclass, or return ``None`` to let it propagate unchanged.

        This is the backend's per-library translator hook (see ``error-handling`` and
        CONTRIBUTING). The default translates nothing; backends override it to map their
        library's known exceptions. It MUST NOT contain a catch-all that converts any
        ``Exception`` — an unrecognized error returns ``None`` so it surfaces and can be
        mapped deliberately.
        """
        return None

    def _wrap_member_stream(
        self, inner: BinaryIO, member_name: str | None, *, lazy: bool = False
    ) -> BinaryIO:
        """Wrap a raw member stream so read/seek errors route through the backend's
        translator and are stamped with format/archive/member context.

        Backends return ``_wrap_member_stream(raw, member.name)`` from ``_open_member`` so
        a decode error surfaces as a stamped ``ArchiveyError`` rather than a raw codec
        exception.
        """
        return ArchiveStream(
            lambda: inner,
            translate=self._translate_exception,
            stamp=lambda exc: self._stamp_error_context(exc, member_name),
            lazy=lazy,
            seekable=is_seekable(inner),
        )

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

    def _require_random_access(self, op: str) -> None:
        """Raise ``UnsupportedOperationError`` if ``op`` (a random-access or
        full-materialization operation) is not allowed on this reader.

        A ``streaming=True`` reader is forward-only: only a single pass of
        ``__iter__``/``stream_members`` (or one ``extract_all``) is allowed. This is
        uniform and format-independent — it does **not** depend on whether a backend
        happens to have an index loaded (use :meth:`get_members_if_available` for a
        no-scan peek at the list instead).
        """
        if self._streaming:
            raise UnsupportedOperationError(
                f"{op} is not available on a streaming (forward-only) reader. "
                f"Iterate with stream_members(), or call get_members_if_available() "
                f"for a no-scan member list.",
            )

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
        if self._streaming and self._members_cache is None:
            # Forward-only: stream directly without caching the whole list.
            yield from self._iter_members()
        else:
            yield from self._get_members_registered()

    def members(self) -> list[ArchiveMember]:
        self._require_random_access("members()")
        return list(self._get_members_registered())

    def get_members_if_available(self) -> list[ArchiveMember] | None:
        """Return the full member list if it is available **without scanning**, else
        ``None``. Safe to call on any reader (including a streaming one).

        Non-``None`` when the list is already materialized (e.g. after an iteration
        pass) or the backend has a true upfront index (``_MEMBER_LIST_UPFRONT`` — a ZIP
        central directory, a 7z header, the filesystem listing). It never triggers a
        scan or consumes the forward pass; when the list would require one it returns
        ``None`` (call :meth:`members` to force materialization, in random mode).
        """
        if self._members_cache is not None:
            return self._members_cache
        if self._MEMBER_LIST_UPFRONT:
            return self._get_members_registered()
        return None

    def __len__(self) -> int:
        self._require_random_access("len()")
        return len(self._get_members_registered())

    def __contains__(self, name: object) -> bool:
        if not isinstance(name, str):
            return False
        self._require_random_access("membership ('in') test")
        self._get_members_registered()
        assert self._members_by_name is not None
        return name in self._members_by_name

    def __getitem__(self, name: str) -> ArchiveMember:
        self._require_random_access("key lookup")
        self._get_members_registered()
        assert self._members_by_name is not None
        try:
            return self._members_by_name[name]
        except KeyError:
            raise KeyError(f"Member {name!r} not found") from None

    def get(self, name: str, default: ArchiveMember | None = None) -> ArchiveMember | None:
        self._require_random_access("key lookup")
        self._get_members_registered()
        assert self._members_by_name is not None
        return self._members_by_name.get(name, default)

    def open(self, member: str | ArchiveMember) -> BinaryIO:
        """Open member for reading. Follows symlinks."""
        # Two independent gates: the access mode (streaming=True forbids random access)
        # and the backend capability (_SUPPORTS_RANDOM_ACCESS, used by the Phase-3
        # open-time fail-fast for non-seekable sources).
        self._require_random_access("open()/read()")
        if not self._SUPPORTS_RANDOM_ACCESS:
            raise UnsupportedOperationError(
                "This reader does not support random access (open()/read()); "
                "iterate with stream_members() instead.",
            )
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
