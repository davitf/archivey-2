"""ISO 9660 reader backed by pycdlib (optional dependency)."""

import io
import logging
import os
import stat as _stat
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, BinaryIO, Iterator, Optional, cast

if TYPE_CHECKING:
    import pycdlib
    import pycdlib.pycdlibexception
else:
    try:
        import pycdlib
        import pycdlib.pycdlibexception
    except ImportError:
        pycdlib = None  # type: ignore[assignment]

from archivey.exceptions import (
    ArchiveCorruptedError,
    ArchiveError,
    ArchiveStreamNotSeekableError,
    PackageNotInstalledError,
)
from archivey.internal.base_reader import BaseArchiveReader
from archivey.internal.io_helpers import is_seekable, is_stream
from archivey.types import (
    ArchiveFormat,
    ArchiveInfo,
    ArchiveMember,
    ContainerFormat,
    MemberType,
)

logger = logging.getLogger(__name__)


class _PyCdlibStream(io.RawIOBase, BinaryIO):
    """Thin wrapper around PyCdlibIO that uses .read() directly.

    PyCdlibIO.readinto() has an EOF-signaling issue when wrapped in
    io.BufferedReader: it doesn't return 0 after EOF, causing BufferedReader
    to keep reading into the ISO sector padding.  Using .read() directly
    avoids this because PyCdlibIO.read() and .readall() correctly clamp to
    the file's logical length.
    """

    def __init__(self, pycdlibio: Any) -> None:
        super().__init__()
        self._raw = pycdlibio

    def read(self, n: int = -1) -> bytes:
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        return cast("bytes", self._raw.read(n))

    def readinto(self, b: bytearray | memoryview) -> int:  # type: ignore[override]
        data = self.read(len(b))
        n = len(data)
        b[:n] = data
        return n

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    def seekable(self) -> bool:
        return cast("bool", self._raw.seekable())

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        return cast("int", self._raw.seek(offset, whence))

    def tell(self) -> int:
        return cast("int", self._raw.tell())

    def close(self) -> None:
        if not self.closed:
            try:
                self._raw.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
        super().close()

    def write(self, b: Any) -> int:
        raise io.UnsupportedOperation("write")


def _dr_date_to_datetime(d: Any) -> datetime:
    """Convert a pycdlib DirectoryRecordDate to a timezone-aware datetime."""
    year = 1900 + d.years_since_1900
    tz = timezone(timedelta(minutes=d.gmtoffset * 15))
    return datetime(
        year, d.month, d.day_of_month, d.hour, d.minute, d.second, tzinfo=tz
    )


def _safe_dr_date(d: Any) -> Optional[datetime]:
    """Convert a DirectoryRecordDate, returning None on any error."""
    if d is None:
        return None
    try:
        return _dr_date_to_datetime(d)
    except (ValueError, AttributeError, TypeError):
        return None


class IsoReader(BaseArchiveReader):
    """Reader for ISO 9660 disc images backed by pycdlib.

    Supports Rock Ridge (preferred), Joliet, and plain ISO 9660 namespaces,
    chosen automatically in that priority order. Streaming mode is not supported
    because ISO 9660 requires sector-addressed seeks for both member listing and
    file data access.
    """

    def _translate_exception(self, e: Exception) -> Optional[ArchiveError]:
        if pycdlib is not None and isinstance(
            e, pycdlib.pycdlibexception.PyCdlibException
        ):
            return ArchiveCorruptedError(f"Error reading ISO image: {e}")
        return None

    def __init__(
        self,
        archive_path: BinaryIO | str | os.PathLike[str],
        format: ArchiveFormat,
        *,
        streaming_only: bool = False,
        pwd: bytes | str | None = None,
    ):
        if format.container != ContainerFormat.ISO:
            raise ValueError(f"Unsupported archive format: {format}")

        if pwd is not None:
            raise ValueError("ISO format does not support password protection")

        if pycdlib is None:
            raise PackageNotInstalledError(
                "pycdlib package is not installed. Install it to open ISO images: "
                "pip install pycdlib"
            )

        # ISO needs seeking even for member listing (sector-addressed tree walk).
        if is_stream(archive_path) and not is_seekable(archive_path):
            raise ArchiveStreamNotSeekableError(
                "ISO images require a seekable source because they use "
                "sector-addressed file access. Pass a file path or a seekable "
                "stream (e.g. an open file or io.BytesIO)."
            )

        super().__init__(
            format=format,
            archive_path=archive_path,
            streaming_only=streaming_only,
            members_list_supported=True,
            pwd=None,
        )

        self._format_info: Optional[ArchiveInfo] = None

        try:
            self._iso = pycdlib.PyCdlib()
            if is_stream(archive_path):
                self._iso.open_fp(archive_path)
            else:
                self._iso.open(str(archive_path))
        except pycdlib.pycdlibexception.PyCdlibException as e:
            raise ArchiveCorruptedError(f"Error opening ISO image: {e}") from e

        self._has_rr: bool = bool(self._iso.rock_ridge)
        self._has_joliet: bool = self._iso.joliet_vd is not None
        self._has_udf: bool = self._iso.udf_root is not None

        # Choose namespace in priority order: Rock Ridge > Joliet > plain ISO9660.
        if self._has_rr:
            self._ns_key = "rr_path"
        elif self._has_joliet:
            self._ns_key = "joliet_path"
        else:
            self._ns_key = "iso_path"

        logger.debug(
            "IsoReader: rr=%s joliet=%s udf=%s ns=%s",
            self._has_rr,
            self._has_joliet,
            self._has_udf,
            self._ns_key,
        )

    def _close_archive(self) -> None:
        if self._iso is not None:
            self._iso.close()
            self._iso = None  # type: ignore[assignment]

    def _ns(self, path: str) -> dict[str, str]:
        """Return the namespace kwargs dict for a given path."""
        return {self._ns_key: path}

    def _strip_version(self, name: str) -> str:
        """Strip ;1 version suffix from plain ISO9660 filenames."""
        if self._ns_key == "iso_path" and name.endswith(";1"):
            return name[:-2]
        return name

    def _join_path(self, dirpath: str, name: str) -> str:
        """Build a full ISO path from directory path and entry name."""
        if dirpath == "/":
            return "/" + name
        return dirpath + "/" + name

    def _dr_to_member(
        self, record: Any, filename: str, full_ns_path: str
    ) -> ArchiveMember:
        """Convert a pycdlib DirectoryRecord to ArchiveMember."""
        rr = getattr(record, "rock_ridge", None)

        # Member type
        if rr is not None and rr.is_symlink():
            member_type = MemberType.SYMLINK
        elif record.is_dir():
            member_type = MemberType.DIR
        else:
            member_type = MemberType.FILE

        # Normalise filename: dirs get trailing slash; strip leading slash
        norm_name = filename
        if member_type == MemberType.DIR and not norm_name.endswith("/"):
            norm_name += "/"

        # Timestamps: prefer Rock Ridge TF entries; fall back to DR date field.
        mtime: Optional[datetime] = _safe_dr_date(getattr(record, "date", None))
        atime: Optional[datetime] = None
        ctime: Optional[datetime] = None

        if rr is not None:
            # Check both dr_entries and ce_entries (overflow area).
            for entries in (rr.dr_entries, rr.ce_entries):
                tf = getattr(entries, "tf_record", None)
                if tf is None:
                    continue
                if mtime is None:
                    mtime = _safe_dr_date(getattr(tf, "modification_time", None))
                if atime is None:
                    atime = _safe_dr_date(getattr(tf, "access_time", None))
                if ctime is None:
                    # prefer creation_time; fall back to attribute_change_time
                    ctime = _safe_dr_date(getattr(tf, "creation_time", None))
                    if ctime is None:
                        ctime = _safe_dr_date(
                            getattr(tf, "attribute_change_time", None)
                        )

        # POSIX permissions and ownership from Rock Ridge PX entry.
        mode: Optional[int] = None
        uid: Optional[int] = None
        gid: Optional[int] = None

        if rr is not None:
            for entries in (rr.dr_entries, rr.ce_entries):
                px = getattr(entries, "px_record", None)
                if px is None:
                    continue
                raw_mode = getattr(px, "posix_file_mode", None)
                if raw_mode is not None:
                    mode = _stat.S_IMODE(raw_mode)
                raw_uid = getattr(px, "posix_user_id", None)
                if raw_uid is not None and raw_uid != 0:
                    uid = raw_uid
                raw_gid = getattr(px, "posix_group_id", None)
                if raw_gid is not None and raw_gid != 0:
                    gid = raw_gid
                break  # found in dr_entries; don't double-apply from ce_entries

        # Symlink target from Rock Ridge SL entry.
        link_target: Optional[str] = None
        if member_type == MemberType.SYMLINK and rr is not None:
            try:
                target_bytes = rr.symlink_path()
                if target_bytes:
                    link_target = target_bytes.decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass

        file_size = getattr(record, "data_length", 0)

        return ArchiveMember(
            filename=norm_name,
            raw_filename=filename,
            file_size=file_size,
            compress_size=file_size,  # ISO has no per-file compression
            mtime_with_tz=mtime,
            atime=atime,
            ctime=ctime,
            type=member_type,
            mode=mode,
            uid=uid,
            gid=gid,
            link_target=link_target,
            crc32=None,
            compression_method="stored",
            encrypted=False,
            raw_info=full_ns_path,  # stored for use by _open_member
        )

    def iter_members_for_registration(self) -> Iterator[ArchiveMember]:
        assert self._iso is not None

        try:
            for dirpath, dirnames, filenames in self._iso.walk(**self._ns("/")):
                # Yield a directory member for every directory except the root.
                if dirpath != "/":
                    rel_dir = dirpath.lstrip("/")
                    try:
                        dr = self._iso.get_record(**self._ns(dirpath))
                        yield self._dr_to_member(dr, rel_dir + "/", dirpath)
                    except Exception as e:  # noqa: BLE001
                        logger.warning(
                            "Could not get record for dir %s: %s", dirpath, e
                        )

                # Yield file (and symlink) members.
                for raw_name in filenames:
                    clean_name = self._strip_version(raw_name)
                    full_path = self._join_path(dirpath, raw_name)
                    rel_path = full_path.lstrip("/")
                    # Replace the basename with the clean (;1-stripped) name.
                    if "/" in rel_path:
                        parent, _ = rel_path.rsplit("/", 1)
                        rel_path = parent + "/" + clean_name
                    else:
                        rel_path = clean_name
                    try:
                        dr = self._iso.get_record(**self._ns(full_path))
                        yield self._dr_to_member(dr, rel_path, full_path)
                    except Exception as e:  # noqa: BLE001
                        logger.warning("Could not get record for %s: %s", full_path, e)

        except pycdlib.pycdlibexception.PyCdlibException as e:
            raise ArchiveCorruptedError(f"Error walking ISO image: {e}") from e

    def _open_member(
        self,
        member: ArchiveMember,
        pwd: bytes | str | None,
        for_iteration: bool,
    ) -> BinaryIO:
        assert self._iso is not None
        ns_path: str = member.raw_info  # type: ignore[assignment]

        try:
            raw = self._iso.open_file_from_iso(**self._ns(ns_path))
            raw.__enter__()  # type: ignore[no-untyped-call]
            return _PyCdlibStream(raw)
        except pycdlib.pycdlibexception.PyCdlibException as e:
            raise ArchiveCorruptedError(
                f"Error opening ISO member {member.filename}: {e}"
            ) from e

    def get_archive_info(self) -> ArchiveInfo:
        assert self._iso is not None

        if self._format_info is None:
            pvd = self._iso.pvd
            volume_id = pvd.volume_identifier.decode("ascii", errors="replace").rstrip()
            interchange_level = getattr(pvd, "interchange_level", 1)

            self._format_info = ArchiveInfo(
                format=self.format,
                version=str(interchange_level),
                is_solid=False,
                comment=volume_id if volume_id else None,
                extra={
                    "rock_ridge": self._iso.rock_ridge if self._has_rr else None,
                    "joliet": self._has_joliet,
                    "udf": self._has_udf,
                    "system_identifier": pvd.system_identifier.decode(
                        "ascii", errors="replace"
                    ).rstrip()
                    if hasattr(pvd, "system_identifier")
                    else None,
                },
            )

        return self._format_info
