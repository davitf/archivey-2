"""ZIP backend on the v2 ABC, backed by the stdlib ``zipfile`` module.

The central directory is read on open, giving O(1) member listing (``INDEXED``) and
direct random access (``DIRECT``) to any member. A non-seekable source fails fast (the
central directory lives at EOF), and split/spanned multi-volume sets are rejected with a
clear "rejoin first" error (see ``format-zip``).
"""

from __future__ import annotations

import io
import re
import stat
import struct
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterator, Mapping, cast

from archivey.internal.cost import (
    AccessCost,
    CostReceipt,
    ListingCost,
    StreamCapability,
)
from archivey.internal.errors import (
    ArchiveyError,
    CorruptionError,
    EncryptionError,
    StreamNotSeekableError,
    UnsupportedFeatureError,
)
from archivey.internal.logs import backends as logger
from archivey.internal.naming import normalize_member_name
from archivey.internal.reader import BaseArchiveReader, ReadBackend
from archivey.internal.registry import register_reader
from archivey.internal.streams.streamtools import is_seekable, is_stream
from archivey.internal.types import (
    ArchiveFormat,
    ArchiveInfo,
    ArchiveMember,
    CompressionAlgorithm,
    CompressionMethod,
    CreateSystem,
    MemberType,
)

# Encoding fallbacks for ZIP metadata stored without the UTF-8 flag.
_ZIP_ENCODINGS = ("utf-8", "cp437", "cp1252", "latin-1")

# A ".z01"/".z42"/… segment name — the obvious signal of a split (multi-volume) ZIP set.
_SPLIT_SEGMENT_RE = re.compile(r"\.z\d{2}$", re.IGNORECASE)

# ZIP compression-method id -> our codec algorithm. Unknown ids map to UNKNOWN rather than
# raising, matching the open-ended CompressionAlgorithm contract.
_ZIP_COMPRESSION_ALGOS: dict[int, CompressionAlgorithm] = {
    0: CompressionAlgorithm.STORED,
    8: CompressionAlgorithm.DEFLATE,
    9: CompressionAlgorithm.DEFLATE64,
    12: CompressionAlgorithm.BZIP2,
    14: CompressionAlgorithm.LZMA,
    93: CompressionAlgorithm.ZSTD,
    98: CompressionAlgorithm.PPMD,
}


def _decode_with_fallback(data: bytes) -> str:
    for encoding in _ZIP_ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _zip_modified(info: zipfile.ZipInfo) -> datetime | None:
    """Return the member's modification time.

    An Extended Timestamp extra field (0x5455) records a real Unix timestamp and takes
    precedence over the 2-second-granularity DOS ``date_time``; when present, the result is
    a timezone-aware UTC ``datetime``. Otherwise a naive ``datetime`` from ``date_time`` is
    returned (``None`` for the "no timestamp" sentinel year 1980-00-00).
    """
    if info.date_time == (1980, 0, 0, 0, 0, 0):
        dos_modtime = None
    else:
        try:
            dos_modtime = datetime(*info.date_time)
        except ValueError:
            logger.warning("Invalid ZIP date_time for %r: %r", info.filename, info.date_time)
            dos_modtime = None

    pos = 0
    extra = info.extra or b""
    while pos + 4 <= len(extra):
        tag, length = struct.unpack("<HH", extra[pos : pos + 4])
        if tag == 0x5455 and length >= 5:  # Extended Timestamp, with flags + at least mtime
            flags = extra[pos + 4]
            if flags & 0x01:  # modification time present
                mod_time = int.from_bytes(extra[pos + 5 : pos + 9], "little")
                if mod_time > 0:
                    return datetime.fromtimestamp(mod_time, tz=timezone.utc)
        pos += 4 + length

    return dos_modtime


class ZipReader(BaseArchiveReader):
    """Reads a ZIP archive via stdlib ``zipfile``."""

    _SUPPORTS_RANDOM_ACCESS = True
    _MEMBER_LIST_UPFRONT = True

    def __init__(
        self,
        source: Path | BinaryIO,
        streaming: bool,
        password: bytes | None,
        encoding: str | None,
        archive_name: str | None,
    ) -> None:
        super().__init__(ArchiveFormat.ZIP, streaming, archive_name)
        self._password = password
        self._encoding = encoding
        # Normalized member name -> ZipInfo, for O(1) data access without re-deriving the
        # stored filename. Populated as members are materialized in _iter_members().
        self._info_by_name: dict[str, zipfile.ZipInfo] = {}

        if is_stream(source) and not is_seekable(source):
            raise StreamNotSeekableError(
                "ZIP archives cannot be read from a non-seekable source (the central "
                "directory lives at the end of the file). Buffer the source to disk or a "
                "BytesIO and reopen.",
                archive_name=archive_name,
            )

        # A split-set segment (name.z01, name.z42, …) cannot be read by stdlib zipfile.
        if archive_name and _SPLIT_SEGMENT_RE.search(archive_name):
            raise UnsupportedFeatureError(
                "Multi-volume (split/spanned) ZIP archives are not supported. Rejoin the "
                "volumes first (e.g. `zip -s 0 split.zip --out whole.zip`) and reopen.",
                archive_name=archive_name,
                source_format=ArchiveFormat.ZIP,
            )

        try:
            # typeshed types ZipFile too narrowly; a binary stream is valid here.
            self._archive: zipfile.ZipFile = zipfile.ZipFile(source, "r")  # type: ignore[arg-type]
        except zipfile.BadZipFile as exc:
            if _looks_like_multivolume(exc):
                raise UnsupportedFeatureError(
                    "This ZIP appears to span multiple disks/volumes, which is not "
                    "supported. Rejoin the volumes first and reopen.",
                    archive_name=archive_name,
                    source_format=ArchiveFormat.ZIP,
                ) from exc
            raise CorruptionError(
                f"Could not open ZIP archive: {exc!r}",
                archive_name=archive_name,
                source_format=ArchiveFormat.ZIP,
            ) from exc

    def _translate_exception(self, exc: Exception) -> ArchiveyError | None:
        if isinstance(exc, zipfile.BadZipFile):
            return CorruptionError(f"Error reading ZIP archive: {exc!r}")
        if isinstance(exc, RuntimeError):
            text = str(exc).lower()
            if "password required" in text:
                return EncryptionError("Password required to read this ZIP member")
            if "bad password" in text:
                return EncryptionError("Wrong password for this ZIP member")
        if isinstance(exc, io.UnsupportedOperation) and "seek" in str(exc):
            return StreamNotSeekableError("ZIP archives require a seekable source")
        if isinstance(exc, NotImplementedError) and "compression method" in str(exc).lower():
            return UnsupportedFeatureError(f"Unsupported ZIP compression method: {exc!r}")
        return None

    def _iter_members(self) -> Iterator[ArchiveMember]:
        for info in self._archive.infolist():
            yield self._to_member(info)

    def _to_member(self, info: zipfile.ZipInfo) -> ArchiveMember:
        full_mode = info.external_attr >> 16
        is_unix = info.create_system == 3
        # Permission bits only; None when no usable Unix mode was stored.
        mode = stat.S_IMODE(full_mode) if (info.external_attr != 0 and is_unix) else None

        if info.is_dir():
            member_type = MemberType.DIRECTORY
        elif is_unix and stat.S_ISLNK(full_mode):
            member_type = MemberType.SYMLINK
        else:
            member_type = MemberType.FILE

        decoded = info.filename
        name = normalize_member_name(decoded.replace("\\", "/"), member_type)
        self._info_by_name[name] = info
        raw_name = info.orig_filename.encode(
            "utf-8" if info.flag_bits & 0x800 else "cp437", errors="surrogateescape"
        )

        algo = _ZIP_COMPRESSION_ALGOS.get(info.compress_type, CompressionAlgorithm.UNKNOWN)

        try:
            create_system = CreateSystem(info.create_system)
        except ValueError:
            create_system = CreateSystem.UNKNOWN

        link_target = None
        if member_type == MemberType.SYMLINK:
            with self._archive.open(info) as f:
                link_target = f.read().decode("utf-8", errors="surrogateescape")

        return ArchiveMember(
            type=member_type,
            name=name,
            raw_name=raw_name,
            size=info.file_size,
            compressed_size=info.compress_size,
            modified=_zip_modified(info),
            mode=mode,
            compression=(CompressionMethod(algo=algo),),
            is_encrypted=bool(info.flag_bits & 0x1),
            comment=_decode_with_fallback(info.comment) if info.comment else None,
            create_system=create_system,
            link_target=link_target,
            extra={"zip.compress_type": info.compress_type},
        )

    def _open_member(self, member: ArchiveMember) -> BinaryIO:
        if not self._info_by_name:
            # Materialize the member list so the name->ZipInfo map is populated (e.g. when
            # opening a member object without a prior listing pass).
            self._get_members_registered()
        info = self._info_by_name[member.name]
        raw = cast(
            "BinaryIO",
            self._archive.open(info, pwd=self._password),
        )
        return self._wrap_member_stream(raw, member.name)

    def _get_archive_info(self) -> ArchiveInfo:
        comment = self._archive.comment
        cost = CostReceipt(
            listing_cost=ListingCost.INDEXED,
            access_cost=AccessCost.DIRECT,
            stream_capability=StreamCapability.SEEKABLE,
            solid_block_count=None,
        )
        return ArchiveInfo(
            format=ArchiveFormat.ZIP,
            format_version=None,
            is_solid=False,  # ZIP is never solid: each member has an independent offset
            member_count=len(self._archive.infolist()),
            comment=_decode_with_fallback(comment) if comment else None,
            is_encrypted=False,  # ZIP has per-member encryption, not header-level
            is_multivolume=False,
            cost=cost,
        )

    def _close_archive(self) -> None:
        self._archive.close()


def _looks_like_multivolume(exc: zipfile.BadZipFile) -> bool:
    text = str(exc).lower()
    return "multi" in text or "disk" in text or "spanned" in text or "split" in text


class ZipReadBackend(ReadBackend):
    """Backend factory for ZIP archives."""

    FORMATS: tuple[ArchiveFormat, ...] = (ArchiveFormat.ZIP,)
    EXTENSIONS: Mapping[str, ArchiveFormat] = {".zip": ArchiveFormat.ZIP}
    MAGIC: tuple[tuple[int, bytes, ArchiveFormat], ...] = (
        (0, b"\x50\x4b\x03\x04", ArchiveFormat.ZIP),  # standard local file header
        (0, b"\x50\x4b\x05\x06", ArchiveFormat.ZIP),  # empty archive (EOCD)
        (0, b"\x50\x4b\x07\x08", ArchiveFormat.ZIP),  # spanned / data-descriptor marker
    )
    REQUIRES_SEEK = True

    def open_read(
        self,
        source: Path | BinaryIO,
        streaming: bool,
        password: bytes | None,
        encoding: str | None,
        archive_name: str | None,
    ) -> ZipReader:
        return ZipReader(source, streaming, password, encoding, archive_name)


register_reader(ZipReadBackend)
