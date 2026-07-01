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

from archivey.cost import (
    AccessCost,
    CostReceipt,
    ListingCost,
    StreamCapability,
)
from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
    EncryptionError,
    StreamNotSeekableError,
    UnsupportedFeatureError,
)
from archivey.internal.base_reader import BaseArchiveReader, ReadBackend
from archivey.internal.logs import backends as logger
from archivey.internal.naming import normalize_member_name
from archivey.internal.registry import register_reader
from archivey.internal.streams.streamtools import is_seekable, is_stream
from archivey.types import (
    ArchiveFormat,
    ArchiveInfo,
    ArchiveMember,
    CompressionAlgorithm,
    CompressionMethod,
    CreateSystem,
    MagicSignature,
    MemberType,
)

# Comment decoding: try UTF-8 first, else fall back to cp437 (the ZIP appnote default,
# which maps every byte and therefore never fails — no further fallbacks are reachable).
_ZIP_ENCODINGS = ("utf-8", "cp437")

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
    raise AssertionError("unreachable: cp437 decodes every byte")


def _zip_timestamps(
    info: zipfile.ZipInfo,
) -> tuple[datetime | None, datetime | None, datetime | None]:
    """Return ``(modified, accessed, created)`` for a member.

    The DOS ``date_time`` gives a 2-second-granularity modification time (``None`` for the
    "no timestamp" sentinel year 1980). An Extended Timestamp extra field (0x5455) records
    real Unix timestamps and takes precedence: its flags byte signals which of
    modification/access/creation times follow (in that order), each a signed 32-bit Unix
    time interpreted as UTC. The central directory typically carries only the modification
    time even when the flags advertise more, so access/creation are often ``None``.
    """
    if info.date_time == (1980, 0, 0, 0, 0, 0):
        modified: datetime | None = None
    else:
        try:
            modified = datetime(*info.date_time)
        except ValueError:
            logger.warning("Invalid ZIP date_time for %r: %r", info.filename, info.date_time)
            modified = None
    accessed: datetime | None = None
    created: datetime | None = None

    extra = info.extra or b""
    pos = 0
    while pos + 4 <= len(extra):
        tag, length = struct.unpack("<HH", extra[pos : pos + 4])
        field = extra[pos + 4 : pos + 4 + length]
        if tag == 0x5455 and field:  # Extended Timestamp: flags byte + present times
            flags = field[0]
            cursor = 1
            for bit in (0x01, 0x02, 0x04):  # mtime, atime, ctime, in order
                if flags & bit and cursor + 4 <= len(field):
                    ts = int.from_bytes(field[cursor : cursor + 4], "little", signed=True)
                    when = datetime.fromtimestamp(ts, tz=timezone.utc)
                    if bit == 0x01:
                        modified = when
                    elif bit == 0x02:
                        accessed = when
                    else:
                        created = when
                    cursor += 4
            break
        pos += 4 + length

    return modified, accessed, created


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

        if is_stream(source) and not is_seekable(source):
            raise StreamNotSeekableError(
                "ZIP archives cannot be read from a non-seekable source: the central "
                "directory lives at the end of the file.",
                archive_name=archive_name,
            )

        # A split-set segment (name.z01, name.z42, …) cannot be read by stdlib zipfile.
        if archive_name and _SPLIT_SEGMENT_RE.search(archive_name):
            raise UnsupportedFeatureError(
                "Multi-volume (split/spanned) ZIP archives are not supported.",
                archive_name=archive_name,
                source_format=ArchiveFormat.ZIP,
            )

        try:
            # `metadata_encoding` (3.11+) decodes names stored without the UTF-8 flag with
            # the caller's encoding instead of the cp437 default (UTF-8-flagged names are
            # unaffected). A wrong explicit encoding may raise UnicodeDecodeError — caller
            # misuse, propagated unchanged like other genuine errors.
            # typeshed types ZipFile too narrowly; a binary stream is valid here.
            self._archive: zipfile.ZipFile = zipfile.ZipFile(  # type: ignore[arg-type]
                source, "r", metadata_encoding=encoding
            )
        except zipfile.BadZipFile as exc:
            if _looks_like_multivolume(exc):
                raise UnsupportedFeatureError(
                    "This ZIP spans multiple disks/volumes, which is not supported.",
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
        # Recover the stored bytes by re-encoding with the codec zipfile decoded with:
        # UTF-8 when the entry's UTF-8 flag is set, else the caller's metadata encoding
        # (when given) or zipfile's cp437 default.
        raw_name = info.orig_filename.encode(
            "utf-8" if info.flag_bits & 0x800 else (self._encoding or "cp437"),
            errors="surrogateescape",
        )

        algo = _ZIP_COMPRESSION_ALGOS.get(info.compress_type, CompressionAlgorithm.UNKNOWN)

        try:
            create_system = CreateSystem(info.create_system)
        except ValueError:
            create_system = CreateSystem.UNKNOWN

        link_target = None
        if member_type == MemberType.SYMLINK:
            # A symlink's target is its (possibly encrypted) file data. Listing must stay
            # usable without a password, so a missing/wrong password leaves link_target
            # unset (following the link later fails with LinkTargetNotFoundError); other
            # errors surface translated like any member-read error.
            try:
                with self._archive.open(info, pwd=self._password) as f:
                    link_target = f.read().decode("utf-8", errors="surrogateescape")
            except (RuntimeError, zipfile.BadZipFile) as exc:
                translated = self._translate_exception(exc)
                if isinstance(translated, EncryptionError):
                    logger.warning(
                        "Cannot read the symlink target of %r without the correct "
                        "password; leaving link_target unset.",
                        info.filename,
                    )
                elif translated is not None:
                    self._stamp_error_context(translated, info.filename)
                    raise translated from exc
                else:
                    raise

        modified, accessed, created = _zip_timestamps(info)
        return ArchiveMember(
            type=member_type,
            name=name,
            raw_name=raw_name,
            size=info.file_size,
            compressed_size=info.compress_size,
            modified=modified,
            accessed=accessed,
            created=created,
            mode=mode,
            compression=(CompressionMethod(algo=algo),),
            is_encrypted=bool(info.flag_bits & 0x1),
            comment=_decode_with_fallback(info.comment) if info.comment else None,
            create_system=create_system,
            link_target=link_target,
            extra={"zip.compress_type": info.compress_type},
            _raw=info,  # carry the ZipInfo so _open_member needs no name/id lookup table
        )

    def _open_member(self, member: ArchiveMember) -> BinaryIO:
        # The member carries its own ZipInfo (`_raw`), so data access needs no name/id map
        # — and a duplicate member name can't resolve to the wrong entry.
        info = member._raw
        assert isinstance(info, zipfile.ZipInfo), "ZIP member is missing its ZipInfo handle"
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
    MAGIC: tuple[MagicSignature, ...] = (
        MagicSignature(0, b"\x50\x4b\x03\x04", ArchiveFormat.ZIP),  # standard local header
        MagicSignature(0, b"\x50\x4b\x05\x06", ArchiveFormat.ZIP),  # empty archive (EOCD)
        MagicSignature(0, b"\x50\x4b\x07\x08", ArchiveFormat.ZIP),  # spanned marker
    )
    REQUIRES_SEEK = True

    def open_read(
        self,
        source: Path | BinaryIO,
        format: ArchiveFormat,
        streaming: bool,
        password: bytes | None,
        encoding: str | None,
        archive_name: str | None,
        strict_eof: bool = False,
    ) -> ZipReader:
        # `format` is always ZIP here (single-format backend); accepted for the uniform
        # ReadBackend signature.
        return ZipReader(source, streaming, password, encoding, archive_name)


register_reader(ZipReadBackend)
