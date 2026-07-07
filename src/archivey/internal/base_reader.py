"""BaseArchiveReader ABC and ReadBackend/WriteBackend ABCs."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Callable, Iterator, Mapping

if TYPE_CHECKING:
    from archivey.internal.password import _PasswordCandidates

from archivey.config import DEFAULT_ARCHIVEY_CONFIG, ArchiveyConfig, ExtractionLimits
from archivey.cost import CostReceipt
from archivey.exceptions import (
    ArchiveyError,
    LinkTargetNotFoundError,
    ReadError,
    UnsupportedOperationError,
)
from archivey.internal.extraction_types import (
    ExtractionPolicy,
    ExtractionProgress,
    ExtractionResult,
    MemberFilter,
    MemberSelectorArg,
    OnError,
    OverwritePolicy,
)
from archivey.internal.naming import resolve_link_target_name
from archivey.internal.selection import normalize_member_selector
from archivey.internal.streams.archive_stream import ArchiveStream
from archivey.internal.streams.counting import CountingReader
from archivey.internal.streams.streamtools import (
    is_seekable,
    is_stream,
    source_byte_size,
)
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
        passwords: _PasswordCandidates | None,
        encoding: str | None,
        archive_name: str | None,
        config: ArchiveyConfig,
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
    ``__iter__``/``stream_members``/``extract_all`` is allowed; ``scan_members()`` may
    finish or return that pass. ``get_members_if_available()`` is a scan-free,
    index-only peek. ``member in reader`` is identity-based and scan-free, so it works in
    either mode; there is no ``__len__``/``__getitem__`` (name lookup is ``get()``).

    **MAY override**:

    - ``_iter_with_data()`` — see its own docstring. The default is correct for
      random-access / indexed backends only; **streaming / solid backends MUST override
      it** (correctness, not just efficiency). Streaming backends that override
      ``_iter_with_data()`` **MUST** route their forward metadata pass through the shared
      instance-held progressive pass (``_begin_forward_pass``) so
      ``scan_members()`` can finish an interrupted pass and the resolved cache is
      finalized on completion. Native 7z/RAR readers will need the same contract.

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
        config: ArchiveyConfig | None = None,
    ) -> None:
        self._format = format
        self._streaming = streaming
        self._archive_name = archive_name
        self._config = config if config is not None else DEFAULT_ARCHIVEY_CONFIG
        self._archive_id = str(id(self))
        # The archive's source, recorded by backends that have one (path or stream);
        # backs the generalized compressed_source_size property below.
        self._source: Path | BinaryIO | None = None
        # A counter wrapping the raw compressed source, set by a backend that decompresses
        # a stream source; backs compressed_bytes_consumed (the live decompression-ratio
        # denominator for a source whose total size is not cheaply knowable).
        self._compressed_input_counter: CountingReader | None = None
        self._members_cache: list[ArchiveMember] | None = None
        self._members_by_name_lists: dict[str, list[ArchiveMember]] | None = None
        self._forward_pass_started: bool = False
        self._progressive_gen: Iterator[ArchiveMember] | None = None
        self._pass_scanned: list[ArchiveMember] = []
        self._pass_by_name_lists: dict[str, list[ArchiveMember]] = {}
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

        When ``streaming=True`` and the backend does not override, this default pulls
        from the shared instance-held progressive pass and opens each file member on
        demand.
        """
        if self._streaming:
            for member in self._begin_forward_pass():
                if member.is_file:
                    yield member, self._open_member(member)
                else:
                    yield member, None
            return
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
        self,
        inner: BinaryIO,
        member_name: str | None,
        *,
        lazy: bool = False,
        size: int | None = None,
    ) -> BinaryIO:
        """Wrap a raw member stream so read/seek errors route through the backend's
        translator and are stamped with format/archive/member context.

        Backends return ``_wrap_member_stream(raw, member.name, size=member.size)`` from
        ``_open_member`` so a decode error surfaces as a stamped ``ArchiveyError`` rather
        than a raw codec exception, and so the handle advertises its decompressed length
        (the fsspec-style ``size``) for cheap nested-archive source sizing.
        """
        return ArchiveStream(
            lambda: inner,
            translate=self._translate_exception,
            stamp=lambda exc: self._stamp_error_context(exc, member_name),
            lazy=lazy,
            seekable=is_seekable(inner),
            size=size,
        )

    @abstractmethod
    def _get_archive_info(self) -> ArchiveInfo: ...

    @abstractmethod
    def _close_archive(self) -> None: ...

    def _get_members_registered(self) -> list[ArchiveMember]:
        """Get all members, assigning member_id and resolving links."""
        if self._members_cache is not None:
            return self._members_cache

        members = list(self._iter_members())
        by_name_lists: dict[str, list[ArchiveMember]] = {}
        for idx, member in enumerate(members):
            if member._member_id is None:
                member._member_id = idx
                member._archive_id = self._archive_id
            self._index_member_name(by_name_lists, member)
        for member in members:
            if member.is_link:
                self._ensure_link_target(member)
        for member in members:
            if member.is_link and member.link_target:
                self._resolve_link(member, by_name_lists)

        self._members_cache = members
        self._members_by_name_lists = by_name_lists
        return members

    def _get_members_index_only(self) -> list[ArchiveMember]:
        """Index-only member list: stamp ids, no link resolution, no member-data reads."""
        members = list(self._iter_members())
        for idx, member in enumerate(members):
            if member._member_id is None:
                member._member_id = idx
                member._archive_id = self._archive_id
        return members

    def _ensure_link_target(self, member: ArchiveMember) -> None:
        """Populate ``link_target`` from member data when needed. Base is a no-op."""
        return

    @staticmethod
    def _index_member_name(
        by_name_lists: dict[str, list[ArchiveMember]], member: ArchiveMember
    ) -> None:
        by_name_lists.setdefault(member.name, []).append(member)

    @staticmethod
    def _target_name_keys(target_name: str) -> tuple[str, ...]:
        if target_name.endswith("/"):
            return (target_name,)
        return (target_name, target_name + "/")

    @staticmethod
    def _latest_prior_named_member(
        target_name: str,
        before_id: int,
        by_name_lists: Mapping[str, list[ArchiveMember]],
    ) -> ArchiveMember | None:
        """Latest member matching ``target_name`` with ``member_id`` strictly before ``before_id``."""
        best: ArchiveMember | None = None
        best_id = -1
        for name in BaseArchiveReader._target_name_keys(target_name):
            for prior in reversed(by_name_lists.get(name, [])):
                prior_id = prior._member_id
                if prior_id is None:
                    continue
                if prior_id < before_id:
                    if prior_id > best_id:
                        best = prior
                        best_id = prior_id
                    break
        return best

    @staticmethod
    def _last_by_exact_name(
        name: str, by_name_lists: Mapping[str, list[ArchiveMember]]
    ) -> ArchiveMember | None:
        candidates = by_name_lists.get(name)
        if not candidates:
            return None
        return candidates[-1]

    @staticmethod
    def _last_named_member(
        target_name: str, by_name_lists: Mapping[str, list[ArchiveMember]]
    ) -> ArchiveMember | None:
        """Last-wins lookup for a link target (tries bare and ``/``-suffixed names)."""
        for name in BaseArchiveReader._target_name_keys(target_name):
            candidates = by_name_lists.get(name)
            if candidates:
                return candidates[-1]
        return None

    @staticmethod
    def _lookup_link_target(
        member: ArchiveMember,
        by_name_lists: Mapping[str, list[ArchiveMember]],
    ) -> ArchiveMember | None:
        """The member ``member``'s link target refers to, or ``None`` if not present.

        Resolves the stored target string to an archive-namespace name first (a symlink
        target is relative to the link's own directory — see
        :func:`resolve_link_target_name`), then looks it up in ``by_name_lists``
        (last-wins for symlinks); directory members carry a trailing ``/`` in their names,
        so both forms are tried.
        """
        if not member.link_target:
            return None
        target_name = resolve_link_target_name(
            member.name, member.link_target, member.type
        )
        if target_name is None:
            return None
        return BaseArchiveReader._last_named_member(target_name, by_name_lists)

    @staticmethod
    def _lookup_hardlink_target(
        member: ArchiveMember,
        by_name_lists: Mapping[str, list[ArchiveMember]],
        *,
        allow_forward_fallback: bool,
    ) -> ArchiveMember | None:
        """Positional hardlink resolution: latest same-named member strictly before ``member``."""
        if not member.link_target:
            return None
        target_name = resolve_link_target_name(
            member.name, member.link_target, member.type
        )
        if target_name is None:
            return None
        before_id = member._member_id
        if before_id is None:
            return None
        found = BaseArchiveReader._latest_prior_named_member(
            target_name, before_id, by_name_lists
        )
        if found is not None:
            return found
        if allow_forward_fallback:
            return BaseArchiveReader._last_named_member(target_name, by_name_lists)
        return None

    def _lookup_link_target_for_member(
        self,
        member: ArchiveMember,
        by_name_lists: Mapping[str, list[ArchiveMember]],
        *,
        allow_forward_fallback: bool = True,
    ) -> ArchiveMember | None:
        if member.type == MemberType.HARDLINK:
            return self._lookup_hardlink_target(
                member,
                by_name_lists,
                allow_forward_fallback=allow_forward_fallback,
            )
        return self._lookup_link_target(member, by_name_lists)

    def _resolve_link(
        self,
        member: ArchiveMember,
        by_name_lists: dict[str, list[ArchiveMember]],
    ) -> None:
        """Resolve link_target to the fully dereferenced link_target_member."""
        visited: set[int] = set()
        current = member

        while current.is_link and current.link_target:
            if current._member_id is None:
                return
            member_id = current._member_id
            if member_id in visited:
                # Cycle detected; leave link_target_member unset (None).
                return
            visited.add(member_id)
            target = self._lookup_link_target_for_member(current, by_name_lists)
            if target is None:
                return
            current = target

        if current is not member:
            member.link_target_member = current

    def _finalize_pass_links(self) -> None:
        """Resolve all links after a streaming forward pass reaches EOF."""
        if self._members_cache is not None:
            return
        for member in self._pass_scanned:
            if member.is_link:
                self._ensure_link_target(member)
        for member in self._pass_scanned:
            if member.is_link and member.link_target:
                self._resolve_link(member, self._pass_by_name_lists)
        self._members_cache = self._pass_scanned
        self._members_by_name_lists = self._pass_by_name_lists

    def _stamp_progressive_member(self, idx: int, member: ArchiveMember) -> None:
        if member._member_id is None:
            member._member_id = idx
            member._archive_id = self._archive_id
        if member.is_link and member.link_target_member is None:
            target = self._lookup_link_target_for_member(
                member,
                self._pass_by_name_lists,
                allow_forward_fallback=False,
            )
            if target is not None and target is not member:
                if target.is_link:
                    target = target.link_target_member
                if target is not None:
                    member.link_target_member = target
        self._index_member_name(self._pass_by_name_lists, member)
        self._pass_scanned.append(member)

    def _begin_forward_pass(self) -> Iterator[ArchiveMember]:
        """Return the shared instance-held progressive pass, creating it if needed."""
        if self._progressive_gen is None:
            self._pass_scanned = []
            self._pass_by_name_lists = {}
            self._progressive_gen = _ProgressivePassIterator(self)
        return self._progressive_gen

    def _guard_forward_pass_entry(self, op: str) -> None:
        if self._streaming and self._forward_pass_started:
            raise UnsupportedOperationError(
                f"{op} is not available after a streaming reader's forward pass has "
                f"started. Call scan_members() for the resolved member list, or "
                f"get_members_if_available() for an index-only peek.",
            )

    def _enter_forward_pass(self, op: str) -> None:
        self._guard_forward_pass_entry(op)
        self._forward_pass_started = True

    # --- Public API ---

    def _require_random_access(self, op: str) -> None:
        """Raise ``UnsupportedOperationError`` if ``op`` (a random-access or
        full-materialization operation) is not allowed on this reader.

        A ``streaming=True`` reader is forward-only: only a single pass of
        ``__iter__``/``stream_members`` (or one ``extract_all``) is allowed. This is
        uniform and format-independent — it does **not** depend on whether a backend
        happens to have an index loaded (use :meth:`scan_members` or
        :meth:`get_members_if_available` for member listing instead).
        """
        if self._streaming:
            raise UnsupportedOperationError(
                f"{op} is not available on a streaming (forward-only) reader. "
                f"Iterate with stream_members(), call scan_members() for the resolved "
                f"member list, or get_members_if_available() for an index-only peek.",
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
        resulting ~1:1 ratio simply never trips the guard. Cheap only — see
        ``source_byte_size``: path ``stat``, a ``size`` attribute (fsspec convention,
        also on archivey's own member/codec streams, enabling nested archives), a
        ``try_get_size()`` index scan, or a ``SEEK_END`` probe restricted to provably
        O(1) types (never a decompressor). Backends record their source in
        ``self._source``; readers without one (directory) or with an unknowable
        source report ``None``.
        """
        return source_byte_size(self._source) if self._source is not None else None

    @property
    def compressed_bytes_consumed(self) -> int | None:
        """Running count of compressed bytes pulled from the archive's outer source so far,
        or ``None`` when nothing is being counted.

        The **live** denominator for extraction's archive-wide decompression-ratio guard
        (see ``safe-extraction``), used when ``compressed_source_size`` is ``None`` — a
        compressed archive whose source size is not cheaply knowable (a non-seekable pipe,
        or a seekable stream that is neither a whitelisted O(1)-seek type nor
        ``.size``-advertising). A backend that decompresses a *stream* source wraps it in a
        ``CountingReader`` and records it here; readers with a knowable source size (a path,
        a sizable stream) leave it ``None`` and rely on the cheaper static ratio instead.
        """
        c = self._compressed_input_counter
        return c.bytes_read if c is not None else None

    def _wrap_compressed_input(self, source: Path | BinaryIO) -> Path | BinaryIO:
        """Wrap a stream source **whose byte size is not cheaply knowable** in a
        ``CountingReader`` (recorded for the live decompression-ratio guard) and return the
        wrapper; return the source unchanged for a path or a sizable stream, whose static
        archive-wide ratio applies instead — exactly the complement of
        ``compressed_source_size``, so one of the two denominators is always available for
        a compressed source. A compressed backend that decompresses a stream source calls
        this on the raw source before handing it to the codec layer, so
        ``compressed_bytes_consumed`` tracks what the decompressor pulls.

        For a *seekable* unsizable source the codec layer may seek and re-read (an index
        scan, an accelerator); re-read bytes are counted again, which only ever inflates
        the denominator — the guard gets weaker, never a false positive.
        """
        if is_stream(source) and source_byte_size(source) is None:
            counter = CountingReader(source)
            self._compressed_input_counter = counter
            return counter
        return source

    def __iter__(self) -> Iterator[ArchiveMember]:
        if self._streaming:
            self._enter_forward_pass("__iter__")
            yield from self._begin_forward_pass()
            return
        yield from self._get_members_registered()

    def members(self) -> list[ArchiveMember]:
        self._require_random_access("members()")
        return list(self._get_members_registered())

    def scan_members(self) -> list[ArchiveMember]:
        if not self._streaming:
            return list(self._get_members_registered())
        if self._members_cache is not None:
            return self._members_cache
        if not self._forward_pass_started:
            self._forward_pass_started = True
        gen = self._begin_forward_pass()
        for _ in gen:
            pass
        assert self._members_cache is not None
        return self._members_cache

    def get_members_if_available(self) -> list[ArchiveMember] | None:
        """Return the full member list if it is available **without scanning**, else
        ``None``. Safe to call on any reader (including a streaming one).

        Index-only: returns a materialized cache after a completed forward pass, or the
        backend's upfront index when ``_MEMBER_LIST_UPFRONT`` is set. It never triggers
        a forward scan, never reads member data, and never consumes the forward pass.
        Link targets stored in member data (e.g. ZIP symlinks) may be unset; use
        :meth:`members` or :meth:`scan_members` for a fully-resolved list.
        """
        if self._members_cache is not None:
            return self._members_cache
        if self._MEMBER_LIST_UPFRONT:
            return self._get_members_index_only()
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
        assert self._members_by_name_lists is not None
        found = self._last_by_exact_name(name, self._members_by_name_lists)
        return found if found is not None else default

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
        else:
            self._get_members_registered()
            # A member object must have been yielded by THIS reader (same identity rule
            # as `member in reader`). Without this check, a member from another archive
            # resolves against the wrong offsets/paths and can silently return the wrong
            # data (e.g. the directory backend would read whatever sits at the same
            # relative path under this reader's root).
            if member._archive_id != self._archive_id:
                raise ValueError(
                    f"Member {member.name!r} does not belong to this reader; open a "
                    f"member yielded by this reader, or look it up by name with "
                    f"reader.get(name)."
                )
        return self._open_with_link_follow(member, visited=set())

    def _open_with_link_follow(
        self,
        member: ArchiveMember,
        visited: set[int],
    ) -> BinaryIO:
        if member.type in (MemberType.SYMLINK, MemberType.HARDLINK):
            if member._member_id is None:
                raise LinkTargetNotFoundError(
                    f"Link target for {member.name!r} is unknown",
                    member_name=member.name,
                )
            member_id = member._member_id
            if member_id in visited:
                raise ReadError(
                    f"Link cycle detected at '{member.name}'",
                    member_name=member.name,
                )
            visited.add(member_id)
            if member.link_target_member is not None:
                return self._open_with_link_follow(member.link_target_member, visited)
            if member.link_target is None:
                self._ensure_link_target(member)
            if member.link_target is None:
                raise LinkTargetNotFoundError(
                    f"Link target for {member.name!r} is unknown",
                    member_name=member.name,
                )
            by_name_lists = self._members_by_name_lists
            target = (
                self._lookup_link_target_for_member(member, by_name_lists)
                if by_name_lists is not None
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
        selector = normalize_member_selector(members)
        if self._streaming:
            self._enter_forward_pass("stream_members()")
        for m, stream in self._iter_with_data():
            if selector is None or selector(m):
                yield m, stream
            elif stream is not None:
                stream.close()

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
        config: ArchiveyConfig | None = None,
        limits: ExtractionLimits | None = None,
    ) -> list[ExtractionResult]:
        """Extract members to dest via the shared ``ExtractionCoordinator``."""
        if self._streaming:
            self._enter_forward_pass("extract_all()")
        # Imported here (not at module top) to keep the import graph tidy: extraction.py
        # type-checks against BaseArchiveReader.
        from archivey.internal.extraction import ExtractionCoordinator

        effective_config = config if config is not None else self._config
        effective_limits = (
            limits if limits is not None else effective_config.extraction_limits
        )
        coordinator = ExtractionCoordinator(
            policy=policy,
            overwrite=overwrite,
            on_error=on_error,
            on_progress=on_progress,
            members=members,
            filter=filter,
            limits=effective_limits,
        )
        return coordinator.run(self, dest)

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


class _ProgressivePassIterator(Iterator[ArchiveMember]):
    """Instance-held streaming pass.

    A generator would be closed (and its post-loop tail skipped) when a consumer
    breaks out of ``for member in reader``; this iterator survives early exit so
    :meth:`BaseArchiveReader.scan_members` can drain the remainder.
    """

    def __init__(self, reader: BaseArchiveReader) -> None:
        self._reader = reader
        self._members_source = reader._iter_members()
        self._next_id = 0
        self._exhausted = False

    def __iter__(self) -> _ProgressivePassIterator:
        return self

    def __next__(self) -> ArchiveMember:
        if self._exhausted:
            raise StopIteration
        try:
            member = next(self._members_source)
        except StopIteration:
            self._exhausted = True
            self._reader._finalize_pass_links()
            raise
        idx = self._next_id
        self._next_id += 1
        self._reader._stamp_progressive_member(idx, member)
        return member
