"""ISO 9660 backend on the v2 ABC, backed by the optional ``pycdlib`` library (``[iso]``).

ISO 9660 images carry up to three parallel filesystem namespaces — plain ISO 9660, Joliet,
and Rock Ridge — with different filename and metadata fidelity. The backend auto-selects the
**richest** available (Rock Ridge > Joliet > plain) and records the choice in
``ArchiveInfo.extra["iso.namespace"]`` so callers can reason about what metadata to expect:
Rock Ridge carries POSIX mode/uid/gid and symlinks; Joliet and plain carry none of those
(those fields are ``None``), and plain names are upper-case 8.3 with a ``;version`` suffix.

The directory tree lives in the header region, giving O(1) (``INDEXED``) listing and
``DIRECT`` random access. A non-seekable source is rejected (``REQUIRES_SEEK``) and write is
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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, BinaryIO, Iterator, Mapping, cast

if TYPE_CHECKING:
    from pycdlib.pycdlibio import PyCdlibIO

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
from archivey.internal.naming import normalize_member_name
from archivey.internal.registry import register_reader
from archivey.internal.streams.streamtools import DelegatingStream
from archivey.types import (
    ArchiveFormat,
    ArchiveInfo,
    ArchiveMember,
    CompressionAlgorithm,
    CompressionMethod,
    MagicSignature,
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

# Only pycdlib's *own* exception is translated to an ArchiveyError. A genuine OSError from
# the underlying handle (file not found, permission, physical media error) is unrelated to
# ISO decoding and MUST propagate unchanged (see error-handling: "Genuine runtime and I/O
# errors are not reclassified"). Built defensively so the module imports without pycdlib.
_PYCDLIB_ERRORS: tuple[type[Exception], ...] = (
    (_pycdlib_exc.PyCdlibException,) if _pycdlib_exc is not None else ()
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
    enter/exit lifecycle paired. ``readinto_passthrough=False`` routes ``readinto`` through
    ``read``: on the supported pycdlib floor (verified on 1.14.0) ``PyCdlibIO.readinto``
    mis-signals EOF — at the member's logical end it returns sector-padding bytes instead of
    0, so repeated ``readinto`` (e.g. a ``BufferedReader`` over it) reads up to the 2 KiB
    sector boundary — while ``PyCdlibIO.read`` always clamps to the logical length. (Newer
    pycdlib, e.g. 1.16, clamps both; the workaround stays for the ``>=1.14`` range and is
    pinned by the cross-format member-stream contract suite.) Read/seek/tell/seekable are
    inherited delegation.
    """

    def __init__(self, raw: "PyCdlibIO") -> None:
        # PyCdlibIO is an io.RawIOBase, which is a BinaryIO at runtime but not by typeshed's
        # nominal hierarchy, so cast at the DelegatingStream boundary.
        super().__init__(cast("BinaryIO", raw), readinto_passthrough=False)
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
        password: bytes | None,
        encoding: str | None,
        archive_name: str | None,
    ) -> None:
        # password rejection is central: open_archive checks ReadBackend.SUPPORTS_PASSWORD.
        super().__init__(format, streaming, archive_name)
        self._source = source
        if pycdlib is None:
            raise PackageNotInstalledError(
                "The 'pycdlib' package is required to read ISO images "
                "(install the 'iso' extra).",
                archive_name=archive_name,
            )

        self._iso = pycdlib.PyCdlib()
        try:
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
        name = normalize_member_name(
            self._display_name(ns_path), member_type, backslash_is_separator=False
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

        return ArchiveMember(
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

    def _open_member(self, member: ArchiveMember) -> BinaryIO:
        ns_path = member._raw
        assert isinstance(ns_path, str), "ISO member is missing its namespace path"
        try:
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
    REQUIRES_SEEK = True
    OPTIONAL_DEPENDENCY = "pycdlib"
    INSTALL_HINT = "pip install archivey[iso]"

    def open_read(
        self,
        source: Path | BinaryIO,
        format: ArchiveFormat,
        streaming: bool,
        password: bytes | None,
        encoding: str | None,
        archive_name: str | None,
        strict_eof: bool = False,
    ) -> IsoReader:
        # `format` is always ISO here (single-format backend); accepted for the uniform
        # ReadBackend signature.
        return IsoReader(source, format, streaming, password, encoding, archive_name)


register_reader(IsoReadBackend)
