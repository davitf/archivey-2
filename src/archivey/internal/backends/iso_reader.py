"""ISO 9660 backend on the v2 ABC, backed by the optional ``pycdlib`` library (``[iso]``).

ISO 9660 images carry up to three parallel filesystem namespaces — plain ISO 9660, Joliet,
and Rock Ridge — with different filename and metadata fidelity. The backend auto-selects the
**richest** available (Rock Ridge > Joliet > plain) and records the choice in
``ArchiveInfo.extra["iso.namespace"]`` so callers can reason about what metadata to expect:
Rock Ridge carries POSIX mode/uid/gid and symlinks; Joliet and plain carry none of those
(those fields are ``None``), and plain names are upper-case 8.3 with a ``;version`` suffix.

The directory tree lives in the header region, giving O(1) (``INDEXED``) listing and
``DIRECT`` random access. A non-seekable source is rejected (random access needs seek,
and the trailing metadata rules out non-seekable streaming too) and write is
out of scope (``UnsupportedOperationError``). The image is read uncompressed in place — a
compressed ``.iso.xz`` is a single-file compressor wrapping the image, not mounted here
(see the seek-heavy-container note in the proposal).

``pycdlib`` addresses the image with absolute offsets (the PVD at 32 KiB etc.), so it
needs the archive to start at ``tell() == 0`` — which ``open_archive`` guarantees by
wrapping any mid-positioned seekable stream in a zero-origin view before handing it to
a backend (the stream-position contract in ``format-detection``).
"""

from __future__ import annotations

import importlib
import re
import stat
import struct
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, BinaryIO, Iterator, Mapping, cast

if TYPE_CHECKING:
    from pycdlib.pycdlibio import PyCdlibIO

from archivey.config import ArchiveyConfig
from archivey.cost import (
    AccessCost,
    CostReceipt,
    ListingCost,
    StreamCapability,
)
from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
    PackageNotInstalledError,
)
from archivey.internal.base_reader import BaseArchiveReader, ReadBackend
from archivey.internal.diagnostics_collector import DiagnosticCollector
from archivey.internal.naming import emit_member_name_normalized, normalize_member_name
from archivey.internal.open_site import OpenSite
from archivey.internal.password import _PasswordCandidates
from archivey.internal.registry import register_reader
from archivey.internal.streams.archive_stream import ArchiveStream
from archivey.internal.streams.streamtools import DelegatingStream, LockedStream
from archivey.types import (
    ArchiveFormat,
    ArchiveInfo,
    ArchiveMember,
    CompressionAlgorithm,
    CompressionMethod,
    MagicSignature,
    MemberStreams,
    MemberType,
)


# pycdlib is an optional *runtime* dependency ([iso] extra): absent in the zero-dep core /
# core-only install. Resolve it dynamically (like the codec layer's optional packages) so the
# module still imports there and absence becomes a clean PackageNotInstalledError. (Typing
# uses the TYPE_CHECKING import above; the dev group carries pycdlib so the checkers resolve it.)
def _optional(name: str) -> ModuleType | None:
    try:
        return importlib.import_module(name)
    except ImportError:  # pragma: no cover - the absent path runs in the core-only CI leg
        return None


pycdlib = _optional("pycdlib")
_pycdlib_exc = _optional("pycdlib.pycdlibexception")
_PYCDLIB_CYCLE_GUARD_INSTALLED = False


class _DequeGuardedCollections:
    """Proxy over the real ``collections`` module that overrides only ``deque``.

    Installed once as ``pycdlib.pycdlib.collections`` so the extent-cycle guard is confined
    to pycdlib's own namespace — no other code in the process ever sees a patched ``deque``,
    unlike a global ``collections.deque`` swap. Every other attribute delegates to the real
    module.
    """

    def __init__(self, real: ModuleType, deque_cls: type) -> None:
        self._real = real
        self.deque = deque_cls

    def __getattr__(self, name: str) -> Any:
        # Reached only for attributes not set in __init__ (i.e. everything but ``deque``).
        return getattr(self._real, name)


def _install_pycdlib_directory_cycle_guard() -> None:
    """Prevent pycdlib from hanging on cyclic ISO/Joliet directory trees.

    pycdlib walks directory trees with a plain ``collections.deque`` and no visit tracking,
    so corrupt directory records that close a cycle (a child extent pointing back at an
    ancestor) loop forever — in any namespace ``open_fp`` walks (plain ISO 9660 PVD, Rock
    Ridge PVD, Joliet SVD, …). The mutation harness found a Joliet case on ``basic-iso``; the
    same mechanism reproduces on plain and Rock Ridge trees (see
    ``test_pycdlib_directory_cycle_does_not_hang``).

    The guard is a ``deque`` subclass that tracks the directory extents scheduled on *that
    instance* and skips re-enqueueing one already seen — valid trees never revisit an extent,
    so this is transparent on well-formed images and no-ops entirely for deques that hold
    anything other than directory records. Because the visit set lives on the instance (not a
    per-walk closure), the subclass is installed **once, permanently**, confined to pycdlib's
    ``collections`` reference: no per-walk swap, no shared mutable state, and concurrent ISO
    opens on separate threads never interfere (each walk builds its own deque instance).
    """
    if pycdlib is None:
        return
    global _PYCDLIB_CYCLE_GUARD_INSTALLED
    if _PYCDLIB_CYCLE_GUARD_INSTALLED:
        return

    import collections

    import pycdlib.pycdlib as pcd_module
    from pycdlib import dr as dr_mod

    real_deque = collections.deque

    class _ExtentGuardedDeque(real_deque):
        """A ``deque`` that drops a directory record whose extent it has already scheduled."""

        def __init__(self, iterable: Any = (), *args: Any, **kwargs: Any) -> None:
            items = list(iterable)
            super().__init__(items, *args, **kwargs)
            # Seed from the initial contents (which bypass ``append``) so a cycle back to a
            # root/seed extent is caught too.
            self._visited_extents: set[int] = {
                item.extent_location()
                for item in items
                if isinstance(item, dr_mod.DirectoryRecord)
            }

        def append(self, dir_record: Any) -> None:
            if isinstance(dir_record, dr_mod.DirectoryRecord):
                extent = dir_record.extent_location()
                if extent in self._visited_extents:
                    return
                self._visited_extents.add(extent)
            super().append(dir_record)

    # setattr (not a direct assignment) so the type checkers don't flag the deliberate
    # module -> proxy substitution against pcd_module.collections's declared Module type.
    setattr(
        pcd_module, "collections", _DequeGuardedCollections(collections, _ExtentGuardedDeque)
    )
    _PYCDLIB_CYCLE_GUARD_INSTALLED = True


_install_pycdlib_directory_cycle_guard()

# Exceptions that mean "this ISO structure is bad", translated to CorruptionError. A
# genuine OSError from the underlying handle (file not found, permission, physical media
# error) is unrelated to ISO decoding and MUST propagate unchanged (see error-handling:
# "Genuine runtime and I/O errors are not reclassified"). pycdlib raises its own
# pycdlib wraps *most* format errors in PyCdlibException, but it is not hardened against
# crafted/truncated input: fuzzing surfaces bare IndexError, struct.error, UnicodeDecodeError,
# AttributeError ("'NoneType' object has no attribute …"), KeyError, and ValueError raised deep
# in its header/path-table/directory-record parsing. At the pycdlib call boundary (this backend
# never does its own attribute access or indexing on pycdlib internals) every one of these means
# "this ISO structure is corrupt", so all are translated to CorruptionError — never a raw
# exception. A genuine OSError from the underlying handle (file not found, permission, media
# error) is deliberately NOT in this set: it is real I/O and MUST propagate unchanged (see
# error-handling: "Genuine runtime and I/O errors are not reclassified"). Built defensively so
# the module imports without pycdlib.
_PYCDLIB_ERRORS: tuple[type[Exception], ...] = (
    ((_pycdlib_exc.PyCdlibException,) if _pycdlib_exc is not None else ())
    + (IndexError, struct.error, UnicodeDecodeError, AttributeError, KeyError, ValueError)
)

# Trailing ";1"/";42" version suffix on a plain ISO 9660 file identifier.
_VERSION_SUFFIX = re.compile(r";\d+$")


def _dr_date_to_datetime(date: Any) -> datetime | None:
    """Convert a pycdlib ``DirectoryRecordDate`` (or Rock Ridge ``TF`` time) to a datetime.

    ``gmtoffset`` is in 15-minute units. Returns ``None`` on a missing or malformed record
    rather than raising — a bad date field must not sink the whole listing.
    """
    if date is None:
        return None
    try:
        tz = timezone(timedelta(minutes=date.gmtoffset * 15))
        return datetime(
            1900 + date.years_since_1900,
            date.month,
            date.day_of_month,
            date.hour,
            date.minute,
            date.second,
            tzinfo=tz,
        )
    except (ValueError, AttributeError, TypeError, OverflowError):
        return None


class _PyCdlibStream(DelegatingStream):
    """Adapt pycdlib's ``PyCdlibIO`` (a one-file context manager) onto ``DelegatingStream``.

    ``PyCdlibIO`` must be *entered* before use — its context manager sets up the read offset —
    so this enters it in ``__init__`` and relies on ``DelegatingStream.close()`` (which calls
    ``inner.close()``, the exact equivalent of ``PyCdlibIO.__exit__``) to exit it, keeping the
    enter/exit lifecycle paired. Read/seek/tell/seekable are inherited delegation.
    """

    def __init__(self, raw: "PyCdlibIO") -> None:
        # PyCdlibIO is an io.RawIOBase, which is a BinaryIO at runtime but not by typeshed's
        # nominal hierarchy, so cast at the DelegatingStream boundary.
        super().__init__(cast("BinaryIO", raw))
        raw.__enter__()  # set up the read offset; close() -> inner.close() exits the context


class IsoReader(BaseArchiveReader):
    """Reads an ISO 9660 image via ``pycdlib`` (Rock Ridge / Joliet / plain)."""

    _SUPPORTS_RANDOM_ACCESS = True
    _MEMBER_LIST_UPFRONT = True  # the directory tree is an in-header index (O(1) listing)

    def __init__(
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
    ) -> None:
        # password rejection is central: open_archive checks ReadBackend.SUPPORTS_PASSWORD.
        super().__init__(
            format,
            streaming,
            archive_name,
            config,
            collector=collector,
            member_streams=member_streams,
            open_site=open_site,
        )
        self._source = source
        # Shared-handle lock: only for CONCURRENT readers (default path takes none).
        self._handle_lock: threading.Lock | None = (
            threading.Lock() if MemberStreams.CONCURRENT in member_streams else None
        )
        if pycdlib is None:
            raise PackageNotInstalledError(
                "The 'pycdlib' package is required to read ISO images "
                "(install the 'iso' extra).",
                archive_name=archive_name,
            )

        self._iso = pycdlib.PyCdlib()
        try:
            if self._handle_lock is not None:
                with self._handle_lock:
                    if isinstance(source, Path):
                        self._iso.open(str(source))
                    else:
                        self._iso.open_fp(source)
            else:
                if isinstance(source, Path):
                    self._iso.open(str(source))
                else:
                    self._iso.open_fp(source)
        except _PYCDLIB_ERRORS as exc:
            translated = self._translate_exception(exc)
            if translated is not None:
                self._stamp_error_context(translated)
                raise translated from exc
            raise

        # Auto-select the richest namespace: Rock Ridge > Joliet > plain ISO 9660.
        if self._iso.has_rock_ridge():
            self._namespace = "rock_ridge"
            self._path_kw = "rr_path"
        elif self._iso.has_joliet():
            self._namespace = "joliet"
            self._path_kw = "joliet_path"
        else:
            self._namespace = "iso9660"
            self._path_kw = "iso_path"

    def _translate_exception(self, exc: Exception) -> ArchiveyError | None:
        if _pycdlib_exc is not None and isinstance(
            exc, _pycdlib_exc.PyCdlibException
        ):
            return CorruptionError(f"Error reading ISO image: {exc!r}")
        # pycdlib does not wrap every parse failure in its own exception type: a truncated
        # or crafted image can raise a bare IndexError/struct.error/ValueError from deep in
        # its header parsing (e.g. `data[offset]` off the end of a short path table). Those
        # are corruption in the ISO structure, not archivey/runtime bugs, so translate them
        # rather than letting a raw IndexError escape. (Found by the corpus mutation harness.)
        if isinstance(
            exc,
            (IndexError, struct.error, UnicodeDecodeError, AttributeError, KeyError, ValueError),
        ):
            # pycdlib choked on corrupt structure (see the _PYCDLIB_ERRORS note). Never a
            # genuine OSError — that is not in this set and propagates unchanged.
            return CorruptionError(f"Error reading ISO image: {exc!r}")
        return None

    # --- listing ------------------------------------------------------------------------

    def _join(self, dirpath: str, name: str) -> str:
        return "/" + name if dirpath == "/" else f"{dirpath}/{name}"

    def _display_name(self, ns_path: str) -> str:
        """The path to show the caller: leading ``/`` stripped, plain-ISO version suffix gone."""
        rel = ns_path.lstrip("/")
        if self._namespace == "iso9660":
            parent, sep, base = rel.rpartition("/")
            rel = parent + sep + _VERSION_SUFFIX.sub("", base)
        return rel

    def _iter_members(self) -> Iterator[ArchiveMember]:
        # Pinned-pycdlib audit (tar-concurrent-open 2.7 / concurrent-member-streams 5.4):
        # walk()/get_record() traverse in-memory parsed catalog records and do not touch
        # _cdfp. Only open_file_from_iso / PyCdlibIO I/O need the handle lock. If a future
        # pycdlib version gains handle access here, lock the complete call.
        try:
            for dirpath, dirnames, filenames in self._iso.walk(**{self._path_kw: "/"}):
                for name in dirnames:
                    yield self._make_member(self._join(dirpath, name))
                for name in filenames:
                    yield self._make_member(self._join(dirpath, name))
        except _PYCDLIB_ERRORS as exc:
            translated = self._translate_exception(exc)
            if translated is not None:
                self._stamp_error_context(translated)
                raise translated from exc
            raise

    def _make_member(self, ns_path: str) -> ArchiveMember:
        record: Any = self._iso.get_record(**{self._path_kw: ns_path})
        rr = getattr(record, "rock_ridge", None)

        if rr is not None and rr.is_symlink():
            member_type = MemberType.SYMLINK
        elif record.is_dir():
            member_type = MemberType.DIRECTORY
        else:
            member_type = MemberType.FILE

        # ISO 9660 / Joliet paths are POSIX-style ("/"): a backslash is a literal character.
        presented = self._display_name(ns_path)
        name = normalize_member_name(
            presented, member_type, backslash_is_separator=False
        )
        raw_name = ns_path.lstrip("/").encode("utf-8", errors="surrogateescape")

        modified, accessed, created = self._timestamps(record, rr)
        mode, uid, gid = self._posix_metadata(rr)
        link_target = self._symlink_target(member_type, rr)

        size = record.data_length if member_type == MemberType.FILE else None
        compression = (
            (CompressionMethod(algo=CompressionAlgorithm.STORED),)
            if member_type == MemberType.FILE
            else ()
        )

        member = ArchiveMember(
            type=member_type,
            name=name,
            raw_name=raw_name,
            size=size,
            compressed_size=size,  # ISO 9660 stores members uncompressed
            modified=modified,
            accessed=accessed,
            created=created,
            mode=mode,
            uid=uid,
            gid=gid,
            link_target=link_target,
            compression=compression,
            is_encrypted=False,
            _raw=ns_path,  # the namespace path, so _open_member needs no lookup table
        )
        emit_member_name_normalized(
            self._diagnostics_collector,
            member=member,
            presented_name=presented,
            archive_name=self._archive_name,
        )
        return member

    def _timestamps(
        self, record: Any, rr: Any
    ) -> tuple[datetime | None, datetime | None, datetime | None]:
        modified = _dr_date_to_datetime(getattr(record, "date", None))
        accessed: datetime | None = None
        created: datetime | None = None
        if rr is not None:
            # Rock Ridge TF entries carry precise POSIX times (in dr_entries, or the CE
            # overflow area). They refine the directory-record date and add access/creation.
            for entries in (rr.dr_entries, rr.ce_entries):
                tf = getattr(entries, "tf_record", None)
                if tf is None:
                    continue
                modified = modified or _dr_date_to_datetime(
                    getattr(tf, "modification_time", None)
                )
                accessed = accessed or _dr_date_to_datetime(
                    getattr(tf, "access_time", None)
                )
                created = (
                    created
                    or _dr_date_to_datetime(getattr(tf, "creation_time", None))
                    or _dr_date_to_datetime(getattr(tf, "attribute_change_time", None))
                )
        return modified, accessed, created

    def _posix_metadata(self, rr: Any) -> tuple[int | None, int | None, int | None]:
        # POSIX mode/uid/gid come only from a Rock Ridge PX record; Joliet/plain carry none,
        # so those namespaces correctly yield (None, None, None).
        if rr is None:
            return None, None, None
        for entries in (rr.dr_entries, rr.ce_entries):
            px = getattr(entries, "px_record", None)
            if px is None:
                continue
            raw_mode = getattr(px, "posix_file_mode", None)
            mode = stat.S_IMODE(raw_mode) if raw_mode is not None else None
            return mode, getattr(px, "posix_user_id", None), getattr(px, "posix_group_id", None)
        return None, None, None

    def _symlink_target(self, member_type: MemberType, rr: Any) -> str | None:
        if member_type != MemberType.SYMLINK or rr is None:
            return None
        try:
            target = rr.symlink_path()
        except _PYCDLIB_ERRORS:
            return None
        return target.decode("utf-8", errors="surrogateescape") if target else None

    # --- data ---------------------------------------------------------------------------

    def _open_member(self, member: ArchiveMember) -> ArchiveStream:
        ns_path = member._raw
        assert isinstance(ns_path, str), "ISO member is missing its namespace path"
        try:
            if self._handle_lock is not None:
                with self._handle_lock:
                    raw = self._iso.open_file_from_iso(**{self._path_kw: ns_path})
                    # Construct under the lock so enter-time pycdlib seek is covered.
                    locked: BinaryIO = LockedStream(
                        _PyCdlibStream(raw), self._handle_lock
                    )
                return self._wrap_member_stream(
                    locked, member.name, size=member.size
                )
            raw = self._iso.open_file_from_iso(**{self._path_kw: ns_path})
            # Construct inside the try so any enter-time pycdlib error is translated too;
            # _PyCdlibStream enters the PyCdlibIO context in its __init__.
            stream = _PyCdlibStream(raw)
        except _PYCDLIB_ERRORS as exc:
            translated = self._translate_exception(exc)
            if translated is not None:
                self._stamp_error_context(translated, member.name)
                raise translated from exc
            raise
        return self._wrap_member_stream(stream, member.name, size=member.size)

    def _get_archive_info(self) -> ArchiveInfo:
        cost = CostReceipt(
            listing_cost=ListingCost.INDEXED,  # directory tree lives in the header region
            access_cost=AccessCost.DIRECT,  # each extent is independently addressable
            stream_capability=StreamCapability.SEEKABLE,
            solid_block_count=None,
        )
        pvd = self._iso.pvd
        volume_id = pvd.volume_identifier.decode("ascii", errors="replace").rstrip()
        interchange_level = getattr(self._iso, "interchange_level", None)
        return ArchiveInfo(
            format=self._format,
            format_version=str(interchange_level) if interchange_level else None,
            is_solid=False,
            member_count=None,  # counting requires walking the tree
            comment=volume_id or None,
            is_encrypted=False,
            is_multivolume=False,
            cost=cost,
            extra={"iso.namespace": self._namespace},
        )

    def _close_archive(self) -> None:
        if self._handle_lock is not None:
            with self._handle_lock:
                self._iso.close()
            return
        self._iso.close()


class IsoReadBackend(ReadBackend):
    """Backend factory for ISO 9660 images (requires the ``[iso]`` extra → ``pycdlib``)."""

    FORMATS: tuple[ArchiveFormat, ...] = (ArchiveFormat.ISO,)
    EXTENSIONS: Mapping[str, ArchiveFormat] = {".iso": ArchiveFormat.ISO}
    # The primary volume descriptor's "CD001" magic sits at offset 32 769; detection peeks the
    # extended 32 774-byte window on demand to find it (see internal/detection.py).
    MAGIC: tuple[MagicSignature, ...] = (
        MagicSignature(32769, b"CD001", ArchiveFormat.ISO),
    )
    # SUPPORTS_STREAMING_NON_SEEKABLE stays False: pycdlib addresses the image by
    # absolute offsets (volume descriptors at 32 KiB), so even a forward-only pass
    # needs a seekable source.
    OPTIONAL_DEPENDENCY = "pycdlib"
    INSTALL_HINT = "pip install archivey[iso]"

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
    ) -> IsoReader:
        # `format` is always ISO here (single-format backend); accepted for the uniform
        # ReadBackend signature.
        return IsoReader(
            source,
            format,
            streaming,
            passwords,
            encoding,
            archive_name,
            config,
            collector=collector,
            member_streams=member_streams,
            open_site=open_site,
        )


register_reader(IsoReadBackend)
