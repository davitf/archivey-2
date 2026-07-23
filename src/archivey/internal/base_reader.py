"""BaseArchiveReader ABC and ReadBackend/WriteBackend ABCs."""

from __future__ import annotations

import threading
import uuid
from abc import ABC, abstractmethod
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    BinaryIO,
    Callable,
    ContextManager,
    Iterator,
    Mapping,
    NoReturn,
)

if TYPE_CHECKING:
    from archivey.internal.password import _PasswordCandidates
    from archivey.measurement import IoStats

from archivey.config import DEFAULT_ARCHIVEY_CONFIG, ArchiveyConfig, ExtractionLimits
from archivey.cost import CostReceipt
from archivey.diagnostics import DiagnosticSummary, ExtractionReport, MemberListReport
from archivey.exceptions import (
    ArchiveyError,
    ArchiveyUsageError,
    CorruptionError,
    EncryptionError,
    LinkTargetNotFoundError,
    ReadError,
    TruncatedError,
    UnsupportedOperationError,
)
from archivey.internal.diagnostics_collector import (
    DiagnosticCollector,
    collector_from_config,
)
from archivey.internal.extraction_types import (
    ExtractionPolicy,
    ExtractionProgress,
    MemberFilter,
    MemberSelectorArg,
    OnError,
    OverwritePolicy,
)
from archivey.internal.listing_limits import ListingLimitTracker
from archivey.internal.measurement import (
    ByteCounter,
    SeekCounter,
    measurement_enabled,
)
from archivey.internal.naming import (
    _warn_for_bidirectional_controls,
    resolve_link_target_name,
)
from archivey.internal.open_site import OpenSite
from archivey.internal.reader_state import ReaderState
from archivey.internal.selection import normalize_member_selector
from archivey.internal.streams.archive_stream import ArchiveStream
from archivey.internal.streams.counting import (
    CountingReader,
    OutputCountingStream,
    SeekCountingStream,
)
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
    HashAlgorithm,
    MagicSignature,
    MemberStreams,
    MemberType,
)


def _apply_last_entry_wins_is_current(members: list[ArchiveMember]) -> None:
    """Stamp is_current for duplicate names (last same-name entry wins).

    Members whose ``name`` appears only once are left unchanged so format-specific
    non-current rows (RAR ``path;N`` file-version history) keep the flag the backend
    already set.
    """
    counts: dict[str, int] = {}
    for member in members:
        counts[member.name] = counts.get(member.name, 0) + 1

    seen: set[str] = set()
    for member in reversed(members):
        if counts[member.name] < 2:
            continue
        if member.name in seen:
            member.is_current = False
        else:
            member.is_current = True
            seen.add(member.name)


@dataclass(frozen=True)
class _Materialized:
    """Published member materialization plus its private lookup index."""

    report: MemberListReport
    by_name_lists: Mapping[str, list[ArchiveMember]]


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
    # Whether open_archive(streaming=True) may open a NON-SEEKABLE source: true for
    # formats walkable front-to-back (TAR, the single-file codecs), false for formats
    # whose index/metadata is not at the front (ZIP's central directory, ISO's
    # descriptors). Random access (streaming=False) always requires a seekable source —
    # repeatable open()/read() cannot be honored over one forward pass, and the library
    # never implicitly buffers — so that side needs no per-backend flag.
    SUPPORTS_STREAMING_NON_SEEKABLE: bool = False
    # Whether this backend's format has encryption a password could unlock. Checked
    # centrally by open_archive(): a password passed for a format that cannot use one is
    # API misuse and is rejected uniformly (backends never see it). ZIP sets this True;
    # the native 7z/RAR readers will too.
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
        collector: DiagnosticCollector | None = None,
        member_streams: MemberStreams = MemberStreams(0),
        open_site: OpenSite | None = None,
    ) -> "BaseArchiveReader":
        """Open ``source`` as ``format`` (the resolved format the registry selected this
        backend for — either detected by ``open_archive`` or supplied by the caller). A
        multi-format backend uses it to pick its concrete codec/variant rather than
        re-inspecting the source.

        ``collector`` is the prospective reader's diagnostic collector (created before
        detection). When omitted, the reader creates one from ``config``.
        """
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
    - ``_open_member(member)``  — return a ``FILE`` member's data stream, wrapped via
      ``_wrap_member_stream`` (every member handle the library hands out is an
      ``ArchiveStream``: uniform error translation/stamping, the ``size``
      advertisement, and room to grow shared handle features).
    - ``_get_archive_info()``   — return the :class:`ArchiveInfo` (format, solidity,
      cost, …).
    - ``_close_archive()``      — release resources (called exactly once, via
      ``close()``).

    **MUST set** when they differ from the defaults (both default ``True``):

    - ``_MEMBER_LIST_UPFRONT``    — does the backend have a true upfront index (central
      directory, 7z header, filesystem listing) that yields the full member list
      *without scanning*? This is the predicate behind :meth:`members_report_if_available`
      (it returns a report when ``True``, else ``None``). It does **not** gate the
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
    finish or return that pass. ``members_report_if_available()`` is a scan-free,
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
    # directory)? Drives members_report_if_available(); does not gate the streaming methods.
    _MEMBER_LIST_UPFRONT: bool = True

    def __init__(
        self,
        format: ArchiveFormat,
        streaming: bool,
        archive_name: str | None,
        config: ArchiveyConfig | None = None,
        collector: DiagnosticCollector | None = None,
        member_streams: MemberStreams = MemberStreams(0),
        open_site: OpenSite | None = None,
    ) -> None:
        self._format = format
        self._streaming = streaming
        self._archive_name = archive_name
        self._config = config if config is not None else DEFAULT_ARCHIVEY_CONFIG
        self._diagnostics_collector = (
            collector if collector is not None else collector_from_config(self._config)
        )
        self._member_streams = member_streams
        self._open_site = open_site
        self._state = ReaderState(member_streams=member_streams, open_site=open_site)
        # A random, opaque identity token (not id(self), which the allocator can reuse
        # after a reader is garbage-collected — a member of a dead reader must never
        # pass another reader's identity check; and not a plain counter, whose small
        # sequential values invite confusion with member_id or hardcoding).
        self._archive_id = uuid.uuid4().hex
        # The archive's source, recorded by backends that have one (path or stream);
        # backs the generalized compressed_source_size property below.
        self._source: Path | BinaryIO | None = None
        # A counter wrapping the raw compressed source, set by a backend that decompresses
        # a stream source; backs compressed_bytes_consumed (the live decompression-ratio
        # denominator for a source whose total size is not cheaply knowable).
        self._compressed_input_counter: CountingReader | None = None
        # Opt-in benchmark counters (see archivey.internal.measurement). When measurement
        # is off these stay None and wrap helpers are identity — zero hot-path overhead.
        self._measure = measurement_enabled()
        self._decompressed_counter: ByteCounter | None = (
            ByteCounter() if self._measure else None
        )
        self._seek_counter: SeekCounter | None = (
            SeekCounter() if self._measure else None
        )
        self._materialized: _Materialized | None = None
        self._listing_tracker = ListingLimitTracker(self._config.listing_limits)
        self._forward_pass_started: bool = False
        # When true, progressive registration enforces ListingLimits (scan_members).
        # stream_members leaves this false so iteration stays the unguarded escape hatch.
        self._progressive_enforce_listing_limits: bool = False
        self._progressive_gen: Iterator[ArchiveMember] | None = None
        self._pass_scanned: list[ArchiveMember] = []
        self._pass_by_name_lists: dict[str, list[ArchiveMember]] = {}
        self._closed = False
        # A backend that shares one underlying handle across member streams (zipfile fp,
        # tarfile fileobj, pycdlib _cdfp) sets this to a lock under CONCURRENT (and TAR
        # also under streaming). Backends acquire it via ``_handle_guard()`` so the "lock
        # when present, no-op otherwise" branch lives in one place. ``None`` = no shared
        # handle to serialize (default readers, path-per-open backends).
        self._handle_lock: threading.Lock | None = None

    def _handle_guard(self) -> ContextManager[object]:
        """Hold the backend's shared-handle lock if one is set, else a no-op context.

        Collapses the repeated ``if self._handle_lock is not None: with lock: … else: …``
        branch that every shared-handle backend otherwise copy-pastes (and can forget to
        keep in sync on one arm).
        """
        return self._handle_lock if self._handle_lock is not None else nullcontext()

    def _raise_translated(
        self,
        exc: Exception,
        member_name: str | None = None,
        *,
        stamp_encryption: bool = True,
    ) -> NoReturn:
        """Translate ``exc`` via ``_translate_exception``, stamp context, and raise.

        The single backend-side error boundary (the out-of-stream counterpart of
        ``ArchiveStream._fail``): an already-typed ``ArchiveyError`` is stamped and
        re-raised as-is; a raw exception the translator recognizes is stamped and raised
        chained to the original; an unrecognized exception propagates unchanged (the
        catch-all-free rule in CONTRIBUTING). ``stamp_encryption=False`` skips member
        stamping for ``EncryptionError`` (ZIP's password errors carry their own message
        and must not be reattributed).
        """
        if isinstance(exc, ArchiveyError):
            self._stamp_error_context(exc, member_name)
            raise exc
        translated = self._translate_exception(exc)
        if translated is None:
            raise exc
        if stamp_encryption or not isinstance(translated, EncryptionError):
            self._stamp_error_context(translated, member_name)
        raise translated from exc

    def _translated_errors(
        self,
        member_name: str | None = None,
        *,
        stamp_encryption: bool = True,
    ) -> ContextManager[None]:
        """Context manager routing any exception from the body through ``_raise_translated``.

        Backends wrap every direct call into their underlying library with this instead
        of hand-rolling the translate/stamp/raise tail per site — the pattern that
        repeatedly drifted (one site catching a narrower tuple than its siblings).
        Take the boundary OUTSIDE ``_handle_guard()`` so translation and stamping never
        run while a shared-handle lock is held. ``BaseException`` (KeyboardInterrupt,
        GeneratorExit) passes through untouched.
        """
        return _TranslatedErrorBoundary(self, member_name, stamp_encryption)

    @property
    def member_streams(self) -> MemberStreams:
        """Declared member-stream capabilities for this reader."""
        return self._member_streams

    def _seek_declared(self) -> bool:
        return self._state.seekable

    def _register_public_stream(self, stream: ArchiveStream) -> ArchiveStream:
        """Admit ``stream`` under the live-stream gate and attach lease release on close."""
        self._state.acquire_live_stream(stream)

        def _on_close() -> None:
            if self._state.release_live_stream(stream):
                self._maybe_teardown()

        stream._on_close = _on_close
        stream._attach_finalizer()
        return stream

    def _internal_member_opens(self):
        """Context manager: library-internal opens are exempt from the live-stream gate."""
        from contextlib import contextmanager

        @contextmanager
        def _cm():
            self._state.begin_internal_opens()
            try:
                yield
            finally:
                self._state.end_internal_opens()

        return _cm()

    def _maybe_teardown(self, pending: Exception | None = None) -> None:
        """Run archive teardown outside lifecycle state once the last lease drops.

        ``pending`` is a stream-close failure to combine with a teardown failure into an
        ``ExceptionGroup`` (D9). Teardown is never retried; the lifecycle is marked
        complete even when ``_close_archive`` fails.
        """
        if not self._state.claim_teardown():
            if pending is not None:
                raise pending
            return
        teardown_exc: Exception | None = None
        try:
            self._close_archive()
        except Exception as exc:  # noqa: BLE001 - combine with pending stream-close failure
            teardown_exc = exc
            self._state.complete_teardown()
        else:
            self._state.complete_teardown()
        if pending is not None and teardown_exc is not None:
            raise ExceptionGroup(
                "member-stream close and archive teardown both failed",
                [pending, teardown_exc],
            )
        if pending is not None:
            raise pending
        if teardown_exc is not None:
            raise teardown_exc

    @abstractmethod
    def _iter_members(self) -> Iterator[ArchiveMember]:
        """Yield every member once, in archive order.

        **Order stability is load-bearing:** repeated calls MUST yield the same members
        in the same order. ``member_id`` is a stamp of enumeration position, and
        selection/extraction match members from *separate* enumerations by
        ``(archive_id, member_id)`` (``members_report_if_available()`` re-enumerates for
        some backends while ``stream_members()`` serves the materialized cache) — an
        order that varies between calls would silently select the wrong members.
        """
        ...

    def _drive_pass_streams(
        self,
        members: Iterator[ArchiveMember],
        *,
        open_member: Callable[[ArchiveMember], ArchiveStream | None],
        close_previous: bool = True,
        cleanup: Callable[[], None] | None = None,
        after_members: Callable[[], None] | None = None,
    ) -> Iterator[tuple[ArchiveMember, ArchiveStream | None]]:
        """Shared close-previous / open / yield / finally driver for ``_iter_with_data``.

        Backends supply an ``open_member`` hook (return ``None`` for non-file members)
        and an optional ``cleanup`` for pass-scoped resources (solid block, ``unrar``
        pipe). ``close_previous=False`` is for TAR streaming, where tarfile invalidates
        the prior ``extractfile`` handle on advance.

        The driver **always** closes the last still-open stream in its ``finally``
        before running ``cleanup`` — never tear down a solid block / pipe under a live
        member wrapper. Double-close with ``stream_members``'s ``finally`` is fine:
        ``ArchiveStream.close`` is idempotent.
        """
        previous: ArchiveStream | None = None
        try:
            for member in members:
                if close_previous and previous is not None:
                    previous.close()
                    previous = None
                stream = open_member(member)
                if stream is not None:
                    previous = stream
                yield member, stream
            if after_members is not None:
                after_members()
        finally:
            if previous is not None:
                previous.close()
            if cleanup is not None:
                cleanup()

    def _iter_with_data(self) -> Iterator[tuple[ArchiveMember, ArchiveStream | None]]:
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

        The yielded streams are **lazy**: the member's data is opened on the first read,
        not at yield time. A consumer that skips a member (a ``stream_members`` selector,
        a filtered extraction) therefore pays nothing for it — no seek to its data, no
        decompressor setup — and an open-time error (e.g. a wrong password) surfaces only
        if the member is actually read, not merely iterated past.
        """
        report: MemberListReport | None = None
        members = self._begin_forward_pass() if self._streaming else None
        if members is None:
            report = self._materialize_members(enforce_listing_limits=False).report
            members = iter(report.members)

        def _open(member: ArchiveMember) -> ArchiveStream | None:
            if member.is_file:
                return self._lazy_member_stream(member)
            return None

        def _raise_report_error() -> None:
            if report is not None and report.error is not None:
                raise report.error

        yield from self._drive_pass_streams(
            members,
            open_member=_open,
            close_previous=True,
            after_members=_raise_report_error,
        )

    def _lazy_member_stream(self, member: ArchiveMember) -> ArchiveStream:
        """A stream over ``member``'s data that defers ``_open_member`` to the first read.

        Closing it before any read never opens the member at all. ``_open_member``
        already returns an ``ArchiveStream``; nesting is collapsed inside
        :meth:`ArchiveStream._ensure_open` so the public handle is a **single**
        wrapper. Deferral does not change what a failed open raises — only when.
        """
        return self._register_public_stream(
            self._wrap_member_stream(
                None,
                member.name,
                open_fn=lambda: self._open_member(member),
                size=member.size,
                # ``_open_member`` already applied output tracking inside its wrap.
                track_output=False,
                seekable=self._seek_declared(),
            )
        )

    @abstractmethod
    def _open_member(self, member: ArchiveMember) -> ArchiveStream:
        """Return a data stream for ``member`` (no link following).

        Backends wrap the raw handle via ``_wrap_member_stream`` so every member
        stream the library hands out is an ``ArchiveStream``.

        **Reentrancy invariant (random-access backends).** For a backend that advertises
        independent member open (``streaming=False``, byte-range / independent access —
        ZIP, single-file, and the future native 7z/RAR readers), this method MUST be a
        function of ``(member, shared source)`` only:

        - it MUST NOT keep unsynchronized per-open scratch on ``self`` that another
          concurrent open can overwrite;
        - synchronized shared bookkeeping (leases, password caches, handle locks) is
          permitted;
        - any archivey-owned byte-range access MUST go through a
          :class:`~archivey.internal.streams.streamtools.SharedSource` view (see
          *Multiple concurrently-open member streams* in ``archive-reading``).

        Immutable, already-materialized state (the member list / name index) MAY be read
        read-only. Backends whose member addressing is owned by an external library that
        already coordinates the shared handle (ISO via ``pycdlib``, ZIP via stdlib
        ``zipfile``) are not required to route through an archivey ``SharedSource`` view,
        but archivey-owned reader state still MUST NOT hold unsynchronized per-open scratch.

        Concurrent ``open`` is supported when the reader was opened with
        ``MemberStreams.CONCURRENT``: first-touch materialization is coordinated
        (wait/share), and after the snapshot is published workers may fan out. Forward-only
        / streaming passes remain single-owner. See ``docs/grab-bag/parallel-reader.md``.
        """
        ...

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

    def _track_decompressed(self, stream: BinaryIO) -> BinaryIO:
        """Wrap ``stream`` to count decoded/output bytes when measurement is on.

        Backends call this on the stream that *is* the decompressor (or stored-data)
        output: member streams for ZIP/TAR/single-file, folder/solid-block streams for
        solid 7z/RAR. Identity when measurement is off.
        """
        counter = self._decompressed_counter
        if counter is None:
            return stream
        return OutputCountingStream(stream, counter)

    def _seek_handle_wrapper(self) -> Callable[[BinaryIO], BinaryIO] | None:
        """Return a ``SharedSource(wrap_handle=…)`` callback when measurement is on."""
        counter = self._seek_counter
        if counter is None:
            return None
        return lambda handle: SeekCountingStream(handle, counter)

    def _track_source_seeks(self, source: Path | BinaryIO) -> Path | BinaryIO:
        """Wrap a seekable BinaryIO source to count seeks; leave paths unchanged.

        Path sources are instrumented via :meth:`_seek_handle_wrapper` on
        ``SharedSource``, or by opening the path and wrapping before a library that
        takes a file object (ZIP). Identity when measurement is off.
        """
        counter = self._seek_counter
        if counter is None or not is_stream(source):
            return source
        return SeekCountingStream(source, counter)

    @property
    def bytes_decompressed(self) -> int:
        """Total decoded/output bytes counted while measurement was enabled, else 0.

        Distinct from :attr:`compressed_bytes_consumed` (compressed *input* pressure for
        the live ratio guard). Internal / harness-facing — not on the public ABC.
        """
        c = self._decompressed_counter
        return c.total if c is not None else 0

    @property
    def source_seek_count(self) -> int:
        """Number of ``seek`` calls on the instrumented archive source, else 0."""
        c = self._seek_counter
        return c.count if c is not None else 0

    def _wrap_member_stream(
        self,
        inner: BinaryIO | None,
        member_name: str | None,
        *,
        open_fn: Callable[[], BinaryIO] | None = None,
        lazy: bool = False,
        size: int | None = None,
        track_output: bool = True,
        seekable: bool | None = None,
        expected_hashes: Mapping[HashAlgorithm, bytes] | None = None,
        expected_size: int | None = None,
        digest_transforms: Mapping[HashAlgorithm, Callable[[bytes], bytes]]
        | None = None,
        verify_member: ArchiveMember | None = None,
    ) -> ArchiveStream:
        """Wrap a raw member stream so read/seek errors route through the backend's
        translator and are stamped with format/archive/member context.

        Backends return ``_wrap_member_stream(raw, member.name, size=member.size)`` from
        ``_open_member`` so a decode error surfaces as a stamped ``ArchiveyError`` rather
        than a raw codec exception, and so the handle advertises its decompressed length
        (the fsspec-style ``size``) for cheap nested-archive source sizing.

        Pass ``open_fn`` (and ``inner=None``) for a lazy open — one ``ArchiveStream``,
        opened on first read. If ``open_fn`` returns an ``ArchiveStream`` (e.g. from
        ``_open_member``), :meth:`ArchiveStream._ensure_open` collapses the nesting
        automatically so callers never see a double wrapper.

        Seekability is gated by ``MemberStreams.SEEKABLE``: without it the wrapper reports
        non-seekable even when ``inner`` could seek. Pass ``seekable=`` to override the
        default (eager: declared ∧ ``is_seekable(inner)``; lazy: declared).

        When measurement is on and ``track_output`` is true (the default), decoded bytes
        delivered through this handle are added to :attr:`bytes_decompressed`. Solid
        backends that already count at the folder/block decode layer pass
        ``track_output=False`` to avoid double-counting. When ``open_fn`` may return an
        ``ArchiveStream``, pass ``track_output=False`` (counting belongs on that inner
        wrap); collapse happens in ``_ensure_open``.

        Pass ``expected_hashes`` / ``expected_size`` to fuse container digest and
        length verification into the returned ``ArchiveStream`` (no separate
        ``VerifyingStream`` layer). A never-opened lazy handle skips verify on
        close, so solid unread members stay cheap.
        """
        if (inner is None) == (open_fn is None):
            raise TypeError("exactly one of inner or open_fn is required")

        if open_fn is not None:
            if track_output:

                def tracked_open(
                    raw_open: Callable[[], BinaryIO] = open_fn,
                ) -> BinaryIO:
                    # open_fn should return a bytes stream when tracking here; nested
                    # ArchiveStream collapse is handled in ArchiveStream._ensure_open
                    # (use track_output=False when open_fn returns ArchiveStream).
                    return self._track_decompressed(raw_open())

                use_open: Callable[[], BinaryIO] = tracked_open
            else:
                use_open = open_fn

            return ArchiveStream(
                use_open,
                translate=self._translate_exception,
                stamp=lambda exc: self._stamp_error_context(exc, member_name),
                lazy=True,
                seekable=(self._seek_declared() if seekable is None else seekable),
                size=size,
                collector=self._diagnostics_collector,
                expected_hashes=expected_hashes,
                expected_size=expected_size,
                digest_transforms=digest_transforms,
                verify_member=verify_member,
                archive_name=self._archive_name,
            )

        assert inner is not None
        if track_output:
            inner = self._track_decompressed(inner)
        return ArchiveStream(
            lambda: inner,
            translate=self._translate_exception,
            stamp=lambda exc: self._stamp_error_context(exc, member_name),
            lazy=lazy,
            seekable=(
                self._seek_declared() and is_seekable(inner)
                if seekable is None
                else seekable
            ),
            size=size,
            collector=self._diagnostics_collector,
            expected_hashes=expected_hashes,
            expected_size=expected_size,
            digest_transforms=digest_transforms,
            verify_member=verify_member,
            archive_name=self._archive_name,
        )

    @abstractmethod
    def _get_archive_info(self) -> ArchiveInfo: ...

    @abstractmethod
    def _close_archive(self) -> None: ...

    def _publish_materialized(
        self,
        members: list[ArchiveMember],
        by_name_lists: dict[str, list[ArchiveMember]],
        *,
        error: ArchiveyError | None,
    ) -> _Materialized:
        if error is not None:
            self._stamp_error_context(error)
        holder = _Materialized(
            report=MemberListReport(
                members=tuple(members),
                error=error,
                diagnostics=self._diagnostics_collector.snapshot(),
            ),
            by_name_lists=by_name_lists,
        )
        self._materialized = holder
        return holder

    def _finalize_links(
        self,
        members: list[ArchiveMember],
        by_name_lists: dict[str, list[ArchiveMember]],
        *,
        error: ArchiveyError | None = None,
        child_scope: bool = False,
        is_current_first: bool = False,
    ) -> None:
        """Resolve hardlink/symlink targets with one double-fault policy.

        When ``error is not None`` (incomplete listing / terminal pass damage), a
        secondary ``CorruptionError`` / ``TruncatedError`` during link-target reads is
        swallowed so the recovered prefix stays publishable. When ``error is None``
        (clean EOF / complete listing), secondary faults propagate.

        Eager materialization stamps ``is_current`` *before* link resolve and uses a
        child scope + internal-open exemption for link-data reads. Progressive finalize
        stamps ``is_current`` *after* link resolve and does not open a child scope —
        preserve those orderings unless a failing test forces convergence.
        """
        if is_current_first:
            _apply_last_entry_wins_is_current(members)
            if not any(member.is_link for member in members):
                return

        def _resolve() -> None:
            for member in members:
                if member.is_link:
                    self._ensure_link_target(member)
            for member in members:
                if member.is_link and member.link_target:
                    self._resolve_link(member, by_name_lists)

        try:
            if child_scope:
                # Also wraps ``_internal_member_opens()`` (live-stream gate exemption) —
                # the two always travel together for eager link-data reads.
                # Link-data reads are a private child scope under an active root when one
                # exists; otherwise they only need the live-stream gate exemption.
                root = self._state.current_root()
                child = (
                    self._state.enter_child(root, "link_reads")
                    if root is not None
                    else None
                )
                try:
                    with self._internal_member_opens():
                        _resolve()
                finally:
                    if child is not None:
                        self._state.release_child(child)
            else:
                _resolve()
        except (CorruptionError, TruncatedError):
            if error is None:
                raise

        if not is_current_first:
            _apply_last_entry_wins_is_current(members)

    def _materialize_members(
        self, *, enforce_listing_limits: bool = True
    ) -> _Materialized:
        """Materialize members into one report plus name index.

        ``CorruptionError`` / ``TruncatedError`` during listing publish an incomplete
        report. Resource limits, interrupts, and all other failures leave the reader
        unmaterialized and propagate unchanged.
        """
        if self._materialized is not None:
            if enforce_listing_limits:
                self._listing_tracker.assert_within_limits()
            return self._materialized

        if not self._state.begin_materialization():
            # Another thread published while we waited (or cache was already ready).
            assert self._materialized is not None
            if enforce_listing_limits:
                self._listing_tracker.assert_within_limits()
            return self._materialized

        members: list[ArchiveMember] = []
        by_name_lists: dict[str, list[ArchiveMember]] = {}
        try:
            self._listing_tracker.reset()
            self._account_archive_comment(enforce=enforce_listing_limits)
            try:
                for idx, member in enumerate(self._iter_members()):
                    self._register_member(
                        idx, member, enforce_listing_limits=enforce_listing_limits
                    )
                    self._index_member_name(by_name_lists, member)
                    members.append(member)
            except (CorruptionError, TruncatedError) as exc:
                # Prefer the original listing error on the report; leave unresolved
                # links as-is when a secondary link-target fault is swallowed.
                self._finalize_links(
                    members,
                    by_name_lists,
                    error=exc,
                    child_scope=True,
                    is_current_first=True,
                )
                holder = self._publish_materialized(
                    members,
                    by_name_lists,
                    error=exc,
                )
                self._state.complete_materialization()
                return holder

            self._finalize_links(
                members,
                by_name_lists,
                error=None,
                child_scope=True,
                is_current_first=True,
            )
            holder = self._publish_materialized(members, by_name_lists, error=None)
            self._state.complete_materialization()
            return holder
        except BaseException:
            # MUST be BaseException, not Exception: a KeyboardInterrupt/MemoryError/
            # SystemExit raised mid-scan (7z folder decode, TAR header walk, a bomb) would
            # otherwise leave cache_state stuck at MATERIALIZING forever — a non-concurrent
            # reader then raises a misleading "materialization already in progress", and a
            # CONCURRENT waiter blocks on the CV with no owner left to notify it. We reset
            # the election state and re-raise so the interrupt still propagates unchanged.
            # (mark_reader_closed's drain path handles BaseException the same way.)
            self._materialized = None
            self._listing_tracker.reset()
            self._state.fail_materialization()
            raise

    def _get_members_registered(
        self, *, enforce_listing_limits: bool = True
    ) -> list[ArchiveMember]:
        """Return the complete member list, raising on incomplete reports."""
        report = self._materialize_members(
            enforce_listing_limits=enforce_listing_limits
        ).report
        if report.error is not None:
            raise report.error
        return list(report.members)

    def _get_members_index_only(self) -> list[ArchiveMember]:
        """Index-only member list: stamp ids, no link resolution, no member-data reads."""
        self._listing_tracker.reset()
        self._account_archive_comment(enforce=True)
        members = list(self._iter_members())
        _apply_last_entry_wins_is_current(members)
        for idx, member in enumerate(members):
            self._register_member(idx, member, enforce_listing_limits=True)
        return members

    def _account_archive_comment(self, *, enforce: bool) -> None:
        comment = self._get_archive_info().comment
        self._listing_tracker.account_archive_comment(comment, enforce=enforce)

    def _register_member(
        self,
        idx: int,
        member: ArchiveMember,
        *,
        enforce_listing_limits: bool = False,
    ) -> None:
        """Assign identity and run backend-independent presentation checks once."""
        if member._member_id is not None:
            # Already stamped (e.g. by ``_get_members_index_only``). Still re-account
            # after a tracker ``reset()`` so totals match the member list — otherwise
            # extract-prep then full materialization on 7z/RAR leaves the tracker at 0
            # while the cache holds every member.
            self._listing_tracker.account_member(member, enforce=enforce_listing_limits)
            return
        member._member_id = idx
        member._archive_id = self._archive_id
        _warn_for_bidirectional_controls(member.name)
        self._listing_tracker.account_member(member, enforce=enforce_listing_limits)

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

    def _finalize_pass_links(self, *, error: ArchiveyError | None = None) -> None:
        """Resolve all links after a streaming forward pass reaches EOF or terminal damage."""
        if self._materialized is not None:
            return
        # scan_members drains with enforcement: refuse to publish an over-limit report.
        if self._progressive_enforce_listing_limits:
            self._listing_tracker.assert_within_limits()
        self._finalize_links(
            self._pass_scanned,
            self._pass_by_name_lists,
            error=error,
            child_scope=False,
            is_current_first=False,
        )
        self._publish_materialized(
            self._pass_scanned,
            self._pass_by_name_lists,
            error=error,
        )

    def _stamp_progressive_member(self, idx: int, member: ArchiveMember) -> None:
        # stream_members: account without enforcing (O(1) escape hatch).
        # scan_members: enforce per registration via ``_progressive_enforce_listing_limits``.
        self._register_member(
            idx,
            member,
            enforce_listing_limits=self._progressive_enforce_listing_limits,
        )
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
            self._listing_tracker.reset()
            self._account_archive_comment(
                enforce=self._progressive_enforce_listing_limits
            )
            self._progressive_gen = _ProgressivePassIterator(self)
        return self._progressive_gen

    def _guard_forward_pass_entry(self, op: str) -> None:
        if self._streaming and self._forward_pass_started:
            raise UnsupportedOperationError(
                f"{op} is not available after a streaming reader's forward pass has "
                f"started. Call scan_members() for the resolved member list, or "
                f"members_report_if_available() for an index-only peek.",
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
        :meth:`members_report_if_available` for member listing instead).
        """
        self._state.require_open(op)
        if self._streaming:
            raise UnsupportedOperationError(
                f"{op} is not available on a streaming (forward-only) reader. "
                f"Iterate with stream_members(), call scan_members() for the resolved "
                f"member list, or members_report_if_available() for an index-only peek.",
            )

    @property
    def format(self) -> ArchiveFormat:
        self._state.require_open("format")
        return self._format

    @property
    def info(self) -> ArchiveInfo:
        self._state.require_open("info")
        return self._get_archive_info()

    @property
    def cost(self) -> CostReceipt:
        self._state.require_open("cost")
        return self._get_archive_info().cost

    @property
    def diagnostics(self) -> DiagnosticSummary:
        """Fresh immutable cumulative snapshot of diagnostics for this reader."""
        self._state.require_open("diagnostics")
        return self._diagnostics_collector.snapshot()

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
        self._state.require_open("compressed_source_size")
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
        self._state.require_open("compressed_bytes_consumed")
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
        self._state.require_open("__iter__")
        if self._streaming:
            token = self._state.acquire_pass("__iter__")
            try:
                self._enter_forward_pass("__iter__")
                yield from self._begin_forward_pass()
            finally:
                self._state.release_pass(token)
            return
        # Random access: iteration just walks the already-published immutable member
        # snapshot, so it must NOT hold a reader-wide pass across consumption — that would
        # reject the common `for m in reader: reader.open(m)` idiom as overlap. Acquire the
        # pass only around materialization (matching members()), then yield the captured
        # snapshot with no pass held so open()/get() inside the loop are admitted.
        token = self._state.acquire_pass("__iter__")
        try:
            report = self._materialize_members().report
        finally:
            self._state.release_pass(token)
        yield from report.members
        if report.error is not None:
            raise report.error

    def members(self) -> list[ArchiveMember]:
        self._require_random_access("members()")
        # Under CONCURRENT, first-touch materialization is coordinated via worker tokens
        # so overlapping members()/open()/get() share one build instead of rejecting.
        # Default readers keep an exclusive pass (single-owner materialization).
        if self._state.concurrent:
            token = self._state.acquire_worker("members")
            try:
                report = self._materialize_members().report
                if report.error is not None:
                    raise report.error
                return list(report.members)
            finally:
                self._state.release_worker(token)
        token = self._state.acquire_pass("members")
        try:
            # Return a shallow copy so callers cannot mutate the published cache container.
            report = self._materialize_members().report
            if report.error is not None:
                raise report.error
            return list(report.members)
        finally:
            self._state.release_pass(token)

    def members_report(self) -> MemberListReport:
        self._state.require_open("members_report()")
        if not self._streaming:
            if self._state.concurrent:
                token = self._state.acquire_worker("members_report")
                try:
                    return self._materialize_members().report
                finally:
                    self._state.release_worker(token)
            token = self._state.acquire_pass("members_report")
            try:
                return self._materialize_members().report
            finally:
                self._state.release_pass(token)

        token = self._state.acquire_pass("members_report")
        try:
            if self._materialized is not None:
                self._listing_tracker.assert_within_limits()
                return self._materialized.report
            # Enforce ListingLimits while draining; stream_members leaves this false.
            self._progressive_enforce_listing_limits = True
            try:
                if not self._forward_pass_started:
                    self._forward_pass_started = True
                gen = self._begin_forward_pass()
                while True:
                    try:
                        next(gen)
                    except StopIteration:
                        break
                    except (CorruptionError, TruncatedError):
                        assert self._materialized is not None
                        break
                assert self._materialized is not None
                self._listing_tracker.assert_within_limits()
                return self._materialized.report
            finally:
                self._progressive_enforce_listing_limits = False
        finally:
            self._state.release_pass(token)

    def scan_members(self) -> list[ArchiveMember]:
        self._state.require_open("scan_members()")
        token = self._state.acquire_pass("scan_members")
        try:
            if not self._streaming:
                report = self._materialize_members().report
                if report.error is not None:
                    raise report.error
                return list(report.members)
            if self._materialized is not None:
                self._listing_tracker.assert_within_limits()
                report = self._materialized.report
                if report.error is not None:
                    raise report.error
                return list(report.members)
            # Enforce ListingLimits while draining; stream_members leaves this false.
            self._progressive_enforce_listing_limits = True
            try:
                if not self._forward_pass_started:
                    self._forward_pass_started = True
                gen = self._begin_forward_pass()
                for _ in gen:
                    pass
                assert self._materialized is not None
                self._listing_tracker.assert_within_limits()
                report = self._materialized.report
                if report.error is not None:
                    raise report.error
                return list(report.members)
            finally:
                self._progressive_enforce_listing_limits = False
        finally:
            self._state.release_pass(token)

    def members_report_if_available(self) -> MemberListReport | None:
        """Return the member-list report if it is available **without scanning**, else
        ``None``. Safe to call on any reader (including a streaming one).

        Index-only: returns a materialized report after a completed forward pass, or the
        backend's upfront index when ``_MEMBER_LIST_UPFRONT`` is set. It never triggers
        a forward scan, never reads member data, and never consumes the forward pass.
        Link targets stored in member data (e.g. ZIP symlinks) may be unset; use
        :meth:`members` or :meth:`scan_members` for a fully-resolved list.
        """
        self._state.require_open("members_report_if_available()")
        if self._materialized is not None:
            self._listing_tracker.assert_within_limits()
            return self._materialized.report
        if self._MEMBER_LIST_UPFRONT:
            return MemberListReport(
                members=tuple(self._get_members_index_only()),
                error=None,
                diagnostics=self._diagnostics_collector.snapshot(),
            )
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

    def get(
        self, name: str, default: ArchiveMember | None = None
    ) -> ArchiveMember | None:
        self._require_random_access("get()")
        token = self._state.acquire_worker("get")
        try:
            materialized = self._materialize_members()
            found = self._last_by_exact_name(name, materialized.by_name_lists)
            if found is not None:
                return found
            if materialized.report.error is not None:
                raise materialized.report.error
            return default
        finally:
            self._state.release_worker(token)

    def open(self, member: str | ArchiveMember) -> ArchiveStream:
        """Open member for reading. Follows symlinks.

        Without ``MemberStreams.CONCURRENT``, at most one member stream may be live.
        With it, concurrent first-touch materialization is coordinated and concurrent
        ``open`` is supported (see :class:`~archivey.types.MemberStreams`).
        Positioning requires ``MemberStreams.SEEKABLE``.
        """
        # Two independent gates: the access mode (streaming=True forbids random access)
        # and the backend capability (_SUPPORTS_RANDOM_ACCESS, used by the Phase-3
        # open-time fail-fast for non-seekable sources).
        self._require_random_access("open()/read()")
        if not self._SUPPORTS_RANDOM_ACCESS:
            raise UnsupportedOperationError(
                "This reader does not support random access (open()/read()); "
                "iterate with stream_members() instead.",
            )
        token = self._state.acquire_worker("open")
        try:
            materialized = self._materialize_members()
            if isinstance(member, str):
                found = self._last_by_exact_name(member, materialized.by_name_lists)
                if found is None:
                    if materialized.report.error is not None:
                        raise materialized.report.error
                    raise KeyError(f"Member {member!r} not found")
                member = found
            else:
                # A member object must have been yielded by THIS reader (same identity rule
                # as `member in reader`). Without this check, a member from another archive
                # resolves against the wrong offsets/paths and can silently return the wrong
                # data (e.g. the directory backend would read whatever sits at the same
                # relative path under this reader's root).
                if member._archive_id != self._archive_id:
                    raise ArchiveyUsageError(
                        f"Member {member.name!r} does not belong to this reader; open a "
                        f"member yielded by this reader, or look it up by name with "
                        f"reader.get(name)."
                    )
            stream = self._open_with_link_follow(member, visited=set())
            return self._register_public_stream(stream)
        finally:
            self._state.release_worker(token)

    def _open_with_link_follow(
        self,
        member: ArchiveMember,
        visited: set[int],
    ) -> ArchiveStream:
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
            materialized = self._materialized
            by_name_lists = (
                materialized.by_name_lists if materialized is not None else None
            )
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
        if member.type in (MemberType.DIRECTORY, MemberType.ANTI, MemberType.OTHER):
            raise ArchiveyUsageError(
                f"Cannot open member {member.name!r}: type is {member.type.value!r} "
                f"(not a file)"
            )
        return self._open_member(member)

    def read(self, member: str | ArchiveMember) -> bytes:
        """Read member data as bytes."""
        with self.open(member) as f:
            return f.read()

    def stream_members(
        self,
        members: MemberSelector = None,
    ) -> Iterator[tuple[ArchiveMember, ArchiveStream | None]]:
        """Yield (member, stream) pairs. members is a selector filter (no transform).

        The yielded stream is owned by the iterator: advancing closes/invalidates the
        previous stream before the next pair is produced.
        """
        self._state.require_open("stream_members()")
        token = self._state.acquire_pass("stream_members")
        current: ArchiveStream | None = None
        try:
            selector = normalize_member_selector(members)
            if self._streaming:
                self._enter_forward_pass("stream_members()")
            for m, stream in self._iter_with_data():
                if current is not None:
                    current.close()
                    current = None
                if selector is None or selector(m):
                    current = stream
                    yield m, stream
                elif stream is not None:
                    stream.close()
        finally:
            if current is not None:
                current.close()
            self._state.release_pass(token)

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
    ) -> ExtractionReport:
        """Extract members to dest via the shared ``ExtractionCoordinator``."""
        self._state.require_open("extract_all()")
        # Check (but do not enter) the single-pass guard here, so a second extract_all
        # on a streaming reader fails with this method's name; the coordinator drives
        # the pass through the public stream_members(), which enters it properly.
        if self._streaming:
            self._guard_forward_pass_entry("extract_all()")
        # Imported here (not at module top) to keep the import graph tidy: extraction.py
        # type-checks against BaseArchiveReader.
        from archivey.internal.extraction import ExtractionCoordinator

        # Listing limits stay on the open-time reader config for the reader lifetime;
        # a per-call config may override extraction_limits / policy / accelerators but
        # must not replace self._config.listing_limits (see archive-reading).
        effective_config = config if config is not None else self._config
        effective_limits = (
            limits if limits is not None else effective_config.extraction_limits
        )
        collector = self._diagnostics_collector
        # This call's report covers only its own extraction-phase events. The one-shot
        # extract() re-snapshots against its own pre-detection watermark to widen the
        # window, so extract_all() never needs to know about that outer scope.
        wm = collector.watermark()
        coordinator = ExtractionCoordinator(
            policy=policy,
            overwrite=overwrite,
            on_error=on_error,
            on_progress=on_progress,
            members=members,
            filter=filter,
            limits=effective_limits,
        )
        token = self._state.acquire_pass("extract_all")
        try:
            # Library-internal member opens (including hardlink recovery) are ungated.
            with self._internal_member_opens():
                results = coordinator.run(self, dest)
        finally:
            self._state.release_pass(token)
        return ExtractionReport(
            results=tuple(results),
            diagnostics=collector.snapshot(since=wm),
        )

    def close(self) -> None:
        """Close the reader.

        Idempotent. Under ``MemberStreams.CONCURRENT``, blocks until in-flight worker
        ``open()`` / ``read()`` / ``get()`` / ``members()`` calls return, then marks the
        reader closed. Escaped open member streams keep their lifecycle leases and remain
        readable until they are closed. A worker that never returns is a caller bug (same
        as any lock); there is no artificial timeout.

        Without ``CONCURRENT``, ``close()`` still raises if a worker call or reader-wide
        pass is actively executing. Teardown runs at most once (possibly deferred until
        the last leased stream closes).
        """
        if self._closed:
            return
        # Only mark closed after mark_reader_closed succeeds (or is a no-op because another
        # thread already closed). Raising on an active pass must leave the reader open.
        if self._state.mark_reader_closed():
            self._closed = True
            self._maybe_teardown()
        else:
            self._closed = True

    def __enter__(self) -> "BaseArchiveReader":
        self._state.require_open("__enter__")
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

    def io_stats(self) -> "IoStats | None":
        """Return I/O counters if measurement is enabled, else ``None``.

        Enable measurement via :func:`archivey.measurement.enable_measurement` around
        the :func:`archivey.open_archive` call. Returns ``None`` when the reader was not
        opened inside an ``enable_measurement()`` context.
        """
        if not self._measure:
            return None
        from archivey.measurement import IoStats

        c_bytes = self._compressed_input_counter
        return IoStats(
            bytes_decompressed=self.bytes_decompressed,
            compressed_bytes_consumed=c_bytes.bytes_read
            if c_bytes is not None
            else None,
            source_seek_count=self.source_seek_count,
        )


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
        self._error: BaseException | None = None

    def __iter__(self) -> _ProgressivePassIterator:
        return self

    def __next__(self) -> ArchiveMember:
        if self._error is not None:
            # The pass previously failed. Its generator is closed, so a plain retry
            # would see StopIteration and finalize the PARTIAL scan as the complete,
            # resolved member cache — scan_members() would then silently return a
            # truncated listing after the caller caught the original error. Fail loud
            # and keep the cache unpublished instead.
            err = ReadError(
                "The archive scan previously failed "
                f"({type(self._error).__name__}); the member list is incomplete and "
                "cannot be resumed. Reopen the archive to retry."
            )
            raise err from self._error
        if self._exhausted:
            raise StopIteration
        try:
            member = next(self._members_source)
        except StopIteration:
            self._exhausted = True
            try:
                self._reader._finalize_pass_links()
            except BaseException as exc:
                # Listing-limit refusal (or link finalize failure) must not leave a
                # half-published cache retryable as success via a second StopIteration.
                self._error = exc
                raise
            raise
        except (CorruptionError, TruncatedError) as exc:
            self._exhausted = True
            try:
                self._reader._finalize_pass_links(error=exc)
            except BaseException as finalize_exc:
                # Non-archive-damage finalize failures (e.g. listing-limit refusal) must
                # not leave a half-published cache. Secondary Corruption/Truncated during
                # link finalization after terminal listing damage is swallowed inside
                # _finalize_links so the recovered prefix stays published.
                self._error = finalize_exc
                raise
            raise
        except BaseException as exc:
            self._error = exc
            raise
        idx = self._next_id
        self._next_id += 1
        try:
            self._reader._stamp_progressive_member(idx, member)
        except BaseException as exc:
            # Registration/link bookkeeping failed mid-pass (e.g. a RAISE-disposition
            # diagnostic or ListingLimits): the pass state is inconsistent, so poison
            # it the same way.
            self._error = exc
            raise
        return member


class _TranslatedErrorBoundary:
    """``with``-boundary that applies ``_raise_translated`` to an escaping exception.

    A plain ``__exit__`` class (not a ``@contextmanager`` generator) so the exception
    handling is explicit: ``BaseException`` that is not an ``Exception`` always passes
    through untouched; an exception ``_raise_translated`` re-raises *unchanged* (already
    typed, or unrecognized by the translator) propagates as the original — with context
    stamped where applicable — and only a genuinely translated exception replaces it,
    chained via ``from``.
    """

    __slots__ = ("_reader", "_member_name", "_stamp_encryption")

    def __init__(
        self,
        reader: BaseArchiveReader,
        member_name: str | None,
        stamp_encryption: bool,
    ) -> None:
        self._reader = reader
        self._member_name = member_name
        self._stamp_encryption = stamp_encryption

    def __enter__(self) -> None:
        return None

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> bool:
        if exc is None or not isinstance(exc, Exception):
            return False
        try:
            self._reader._raise_translated(
                exc,
                self._member_name,
                stamp_encryption=self._stamp_encryption,
            )
        except BaseException as raised:
            if raised is exc:
                # Already typed (stamped in place) or unrecognized: let the ORIGINAL
                # propagate from the with-body rather than re-raising from here, which
                # would tack an extra frame onto its traceback.
                return False
            raise
