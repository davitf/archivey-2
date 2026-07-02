"""BaseArchiveReader ABC and ReadBackend/WriteBackend ABCs."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO, Callable, Iterator, Mapping

from archivey.cost import CostReceipt
from archivey.exceptions import (
    ArchiveyError,
    LinkTargetNotFoundError,
    UnsupportedOperationError,
)
from archivey.internal.naming import resolve_link_target_name
from archivey.internal.streams.archive_stream import ArchiveStream
from archivey.internal.streams.streamtools import is_seekable, source_byte_size
from archivey.reader import ArchiveReader, MemberSelector
from archivey.types import (
    ArchiveFormat,
    ArchiveInfo,
    ArchiveMember,
    MagicSignature,
    MemberType,
)


class ReadBackend(ABC):
    """Stateless factory for creating ArchiveReader instances.

    Each backend declares its magic and extensions **as data**, and every entry names
    the :class:`ArchiveFormat` it implies, so a *multi-format* backend (the single
    ``SingleFileBackend``, the TAR backend over ``TAR`` + its compressed combos) can map
    each signal to the right format. The detector aggregates these across all registered
    backends; backends carry no ``detect()`` method.
    """

    FORMATS: tuple[ArchiveFormat, ...]
    # ".gz" -> ArchiveFormat.GZ
    EXTENSIONS: Mapping[str, ArchiveFormat] = {}
    # Exact magic-byte signals as data (offset, bytes, format), accepted on the byte match.
    MAGIC: tuple[MagicSignature, ...] = ()
    # Formats this backend reads that have no exact magic and are recognized by a content
    # probe instead: (format, probe) pairs, where the probe inspects a peeked prefix and
    # returns True on a match (Brotli has no signature; zlib's 2-byte header is too weak).
    CONTENT_PROBES: tuple[tuple[ArchiveFormat, Callable[[bytes], bool]], ...] = ()
    REQUIRES_SEEK: bool = False
    # When True, open_archive(streaming=True) may open a non-seekable source (TAR only).
    SUPPORTS_STREAMING_NON_SEEKABLE: bool = False
    # Whether this backend's format has encryption a password could unlock. Checked
    # centrally by open_archive(): a password passed for a format that cannot use one is
    # API misuse and is rejected uniformly (backends never see it). ZIP sets this True;
    # the native 7z/RAR readers (Phase 7) will too.
    SUPPORTS_PASSWORD: bool = False
    # Name of the optional dependency this backend needs (e.g. "pycdlib"); the registry
    # derives availability centrally from whether it imports. ``None`` for core backends.
    OPTIONAL_DEPENDENCY: str | None = None
    # Human-readable install hint surfaced when the dependency is absent
    # (e.g. "pip install archivey[iso]").
    INSTALL_HINT: str | None = None

    @abstractmethod
    def open_read(
        self,
        source: Path | BinaryIO,
        format: ArchiveFormat,
        streaming: bool,
        password: bytes | None,
        encoding: str | None,
        archive_name: str | None,
        strict_eof: bool = False,
    ) -> "BaseArchiveReader":
        """Open ``source`` as ``format`` (the resolved format the registry selected this
        backend for — either detected by ``open_archive`` or supplied by the caller). A
        multi-format backend uses it to pick its concrete codec/variant rather than
        re-inspecting the source."""
        ...


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
    is forward-only, so ``members``/``get``/``open``/``read`` all raise
    ``UnsupportedOperationError`` — uniformly, not per-backend. Only a single pass of
    ``__iter__``/``stream_members``/``extract_all`` (and ``get_members_if_available``)
    is allowed. ``member in reader`` is identity-based and scan-free, so it works in
    either mode; there is no ``__len__``/``__getitem__`` (name lookup is ``get()``).

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
        # The archive's source, recorded by backends that have one (path or stream);
        # backs the generalized compressed_source_size property below.
        self._source: Path | BinaryIO | None = None
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

    @staticmethod
    def _lookup_link_target(
        member: ArchiveMember, by_name: Mapping[str, ArchiveMember]
    ) -> ArchiveMember | None:
        """The member ``member``'s link target refers to, or ``None`` if not present.

        Resolves the stored target string to an archive-namespace name first (a symlink
        target is relative to the link's own directory — see
        :func:`resolve_link_target_name`), then looks it up; directory members carry a
        trailing ``/`` in their names, so both forms are tried.
        """
        if not member.link_target:
            return None
        target_name = resolve_link_target_name(
            member.name, member.link_target, member.type
        )
        if target_name is None:
            return None
        target = by_name.get(target_name)
        if target is None and not target_name.endswith("/"):
            target = by_name.get(target_name + "/")
        return target

    def _resolve_link(
        self,
        member: ArchiveMember,
        by_name: dict[str, ArchiveMember],
    ) -> None:
        """Resolve link_target to link_target_member using cycle detection."""
        visited: set[str] = set()
        current = member

        while current.is_link and current.link_target:
            if current.name in visited:
                # Cycle detected; leave link_target_member unset (None) rather than
                # pointing at an intermediate link in the cycle.
                return
            visited.add(current.name)
            target = self._lookup_link_target(current, by_name)
            if target is None:
                # Missing/unresolvable target - leave link_target_member as None
                return
            current = target

        # Set on the original member (the final resolved target or None on cycle)
        if current is not member:
            member.link_target_member = current

    def _register_progressively(
        self, members: Iterator[ArchiveMember]
    ) -> Iterator[ArchiveMember]:
        """Stamp ids and resolve *backward-pointing* links during a single forward pass.

        Backs the streaming iteration paths. Hardlinks always refer to an earlier member
        (the TAR model — see ``archive-reading``), so they resolve during the pass; a
        symlink to an earlier member resolves too, while a forward-pointing one stays
        unresolved (``link_target_member`` ``None``). A backend that already stamps ids
        is left untouched (the stamp only fills unset fields).
        """
        by_name: dict[str, ArchiveMember] = {}
        for idx, member in enumerate(members):
            if member._member_id is None:
                member._member_id = idx
                member._archive_id = self._archive_id
            if member.is_link and member.link_target_member is None:
                target = self._lookup_link_target(member, by_name)
                if target is not None and target is not member:
                    # An earlier link's own resolution is already final; reuse it (an
                    # unresolved earlier link yields None, i.e. stays unresolved).
                    if target.is_link:
                        target = target.link_target_member
                    if target is not None:
                        member.link_target_member = target
            by_name[member.name] = member
            yield member

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

    @property
    def compressed_source_size(self) -> int | None:
        """Byte size of the archive's source when cheaply knowable, else ``None``.

        The denominator for extraction's archive-wide decompression-ratio guard (see
        ``safe-extraction``): for zip/7z/rar/compressed-tar the source size *is* the
        compressed size, and for a plain tar or other uncompressed container the
        resulting ~1:1 ratio simply never trips the guard. Covers path sources
        (``stat``), streams advertising a ``size`` attribute (fsspec), and seekable
        streams (a ``SEEK_END``/restore probe) — see ``source_byte_size``. Backends
        record their source in ``self._source``; readers without one (directory) or
        with a non-seekable sizeless stream report ``None``.
        """
        return source_byte_size(self._source) if self._source is not None else None

    def __iter__(self) -> Iterator[ArchiveMember]:
        if self._streaming and self._members_cache is None:
            # Forward-only: stream directly without caching the whole list, stamping ids
            # and resolving backward-pointing links progressively (a forward-pointing
            # symlink stays unresolved — a single pass cannot see later members).
            yield from self._register_progressively(self._iter_members())
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

    def __contains__(self, member: object) -> bool:
        # Identity membership for ArchiveMembers: O(1), no scan, so it works in any
        # access mode. This method must exist even though it is a convenience — without
        # a __contains__, the `in` operator falls back to iterating __iter__, which
        # would silently consume a streaming reader's single forward pass (and compare
        # members by value). Strings are rejected: name lookup is get().
        if isinstance(member, ArchiveMember):
            return member._archive_id == self._archive_id
        raise TypeError(
            f"'in <ArchiveReader>' tests whether an ArchiveMember belongs to this "
            f"reader (by identity); to look up a member by name use reader.get(name). "
            f"Got {type(member).__name__}.",
        )

    def get(self, name: str, default: ArchiveMember | None = None) -> ArchiveMember | None:
        self._require_random_access("get()")
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
            found = self.get(member)
            if found is None:
                raise KeyError(f"Member {member!r} not found")
            member = found
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
                self._lookup_link_target(member, self._members_by_name)
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
